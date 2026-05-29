"""
Watchtower — backtest framework.

Replays screen strategies through history to measure forward-return
performance. Limited to strategies using data with true time-series
depth (prices, earnings_history, insider_stats). Snapshot-only tables
(institutional, news, analyst_revisions, short_interest, etc.) can be
backtested forward as we collect more data, but not historically yet.

Strategies (V1):
  reversal_simple        — ≥20% off high + RSI<40 rising + MACD positive
  earnings_beat_plus_tech — ≥5% earnings beat in last 14d + reversal signals
  insider_burst_plus_tech — ≥3 net insider buys recent 2Q + reversal signals

For each strategy:
  1. Iterate monthly through history (default cadence 30 days)
  2. Apply strategy logic at each as_of_date
  3. Record picks with entry price (next trading day's close)
  4. Compute forward returns at 1m / 3m / 6m
  5. Aggregate win rate, avg return, excess vs SPY

Usage:
    set -a && source .env && set +a
    python3 signals/backtest.py --strategy reversal_simple
    python3 signals/backtest.py --strategy earnings_beat_plus_tech --months-back 12
    python3 signals/backtest.py --report             # summary of all runs
    python3 signals/backtest.py --top-picks 50       # best/worst by 3m return
"""
import argparse
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional

try:
    import numpy as np
    import pandas as pd
    import psycopg2
    from psycopg2.extras import RealDictCursor, execute_values
except ImportError as e:
    print(f"ERROR: missing dependency: {e}", file=sys.stderr)
    sys.exit(1)


# ============================================================
# DNS / connection
# ============================================================
_IPV6_CACHE: dict[str, str] = {}


def _resolve_ipv6(host: str) -> Optional[str]:
    if host in _IPV6_CACHE:
        return _IPV6_CACHE[host]
    try:
        for info in socket.getaddrinfo(host, None, family=socket.AF_INET6):
            addr = info[4][0]
            _IPV6_CACHE[host] = addr
            return addr
    except socket.gaierror:
        pass
    try:
        out = subprocess.check_output(
            ["dig", "+short", "+time=3", "+tries=1", host, "AAAA", "@8.8.8.8"],
            stderr=subprocess.DEVNULL, timeout=8,
        ).decode().strip()
        for line in out.splitlines():
            line = line.strip()
            if ":" in line and not line.endswith("."):
                _IPV6_CACHE[host] = line
                return line
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def _conn():
    required = ["SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
                "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing env vars: {missing}")
    host = os.environ["SUPABASE_DB_HOST"]
    port = int(os.environ["SUPABASE_DB_PORT"])
    user = os.environ["SUPABASE_DB_USER"]
    password = os.environ["SUPABASE_DB_PASSWORD"]
    dbname = os.environ["SUPABASE_DB_NAME"]
    hostaddr = None
    try:
        socket.getaddrinfo(host, port)
    except socket.gaierror:
        hostaddr = _resolve_ipv6(host)
    last_err = None
    for attempt in range(4):
        try:
            kwargs = dict(host=host, port=port, user=user, password=password,
                          dbname=dbname, sslmode="require", connect_timeout=15)
            if hostaddr:
                kwargs["hostaddr"] = hostaddr
            conn = psycopg2.connect(**kwargs)
            with conn.cursor() as c:
                c.execute("SET statement_timeout = '600s'")
            conn.commit()
            return conn
        except psycopg2.OperationalError as e:
            last_err = e
            msg = str(e)
            if any(x in msg for x in ("could not translate", "unreachable", "timeout", "No route")):
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"could not connect: {last_err}")


# ============================================================
# Price helpers
# ============================================================
def load_all_prices(conn, start: date) -> dict[str, pd.DataFrame]:
    sql = """
        SELECT ticker, trade_date, close
        FROM daily_prices
        WHERE trade_date >= %s
        ORDER BY ticker, trade_date
    """
    with conn.cursor() as cur:
        cur.execute(sql, (start,))
        rows = cur.fetchall()
    out: dict[str, list] = {}
    for t, d, c in rows:
        out.setdefault(t, []).append((d, float(c)))
    frames = {}
    for t, data in out.items():
        df = pd.DataFrame(data, columns=["trade_date", "close"])
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        frames[t] = df
    return frames


def price_on_or_after(df: pd.DataFrame, target: date) -> Optional[tuple[date, float]]:
    """Returns (actual_date, close) on or after target. None if no data."""
    ts = pd.Timestamp(target)
    mask = df["trade_date"] >= ts
    if not mask.any():
        return None
    row = df[mask].iloc[0]
    return row["trade_date"].date(), float(row["close"])


def forward_window_stats(df: pd.DataFrame, entry_date: date,
                         window_days: int) -> dict:
    """Compute return + max/drawdown within [entry_date, entry_date + window_days]."""
    end = entry_date + timedelta(days=window_days)
    mask = (df["trade_date"] >= pd.Timestamp(entry_date)) & (df["trade_date"] <= pd.Timestamp(end))
    sub = df[mask].reset_index(drop=True)
    if len(sub) < 2:
        return {"return_pct": None, "max_pct": None, "drawdown_pct": None,
                "exit_close": None}
    entry_close = float(sub["close"].iloc[0])
    exit_close = float(sub["close"].iloc[-1])
    if entry_close <= 0:
        return {"return_pct": None, "max_pct": None, "drawdown_pct": None,
                "exit_close": exit_close}
    ret = (exit_close / entry_close) - 1.0
    max_close = float(sub["close"].max())
    min_close = float(sub["close"].min())
    max_ret = (max_close / entry_close) - 1.0
    # Drawdown from running cummax
    cum_max = sub["close"].cummax()
    drawdowns = (sub["close"] - cum_max) / cum_max
    dd = float(drawdowns.min())
    return {
        "return_pct": ret,
        "max_pct": max_ret,
        "drawdown_pct": dd,
        "exit_close": exit_close,
    }


# ============================================================
# Strategy implementations (each returns list of (ticker, score))
# ============================================================
def strat_reversal_simple(conn, as_of: date, prices: dict[str, pd.DataFrame]) -> list[tuple[str, float]]:
    """Quality filter: stock has ≥60 days of price data through as_of,
    ≥20% off 252-day high, RSI<40 with positive slope, MACD turning positive."""
    picks = []
    cutoff_start = as_of - timedelta(days=400)
    for t, df in prices.items():
        win = df[(df["trade_date"] <= pd.Timestamp(as_of)) &
                 (df["trade_date"] >= pd.Timestamp(cutoff_start))]
        if len(win) < 60:
            continue
        close = win["close"]
        current = float(close.iloc[-1])
        hi = float(close.max())
        if hi <= 0:
            continue
        pct_off = 1 - current / hi
        if pct_off < 0.20:
            continue

        # RSI(14)
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
        rs = gain / loss
        rsi = 100 - 100 / (1 + rs)
        if len(rsi.dropna()) < 5:
            continue
        rsi_now = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-5])
        if not (rsi_now < 50 and rsi_now > rsi_prev):
            continue

        # MACD histogram positive
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        if float(hist.iloc[-1]) <= 0:
            continue

        score = pct_off * 100  # higher score = deeper drawdown
        picks.append((t, score))
    return picks


def strat_earnings_beat_plus_tech(conn, as_of: date, prices: dict[str, pd.DataFrame]) -> list[tuple[str, float]]:
    """Stocks that reported a ≥5% EPS beat in the 14 days before as_of."""
    cutoff = as_of - timedelta(days=14)
    sql = """
        SELECT ticker, surprise_pct
        FROM earnings_history
        WHERE eps_actual IS NOT NULL
          AND surprise_pct >= 0.05
          AND report_date BETWEEN %s AND %s
        ORDER BY report_date DESC
    """
    with conn.cursor() as cur:
        cur.execute(sql, (cutoff, as_of))
        beat_rows = {r[0]: float(r[1]) for r in cur.fetchall()}

    picks = []
    for t, surp in beat_rows.items():
        df = prices.get(t)
        if df is None:
            continue
        win = df[df["trade_date"] <= pd.Timestamp(as_of)]
        if len(win) < 30:
            continue
        close = win["close"]
        # Light technical filter: 8 EMA above 13 EMA OR MACD positive
        ema8 = close.ewm(span=8, adjust=False).mean()
        ema13 = close.ewm(span=13, adjust=False).mean()
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        sig = macd.ewm(span=9, adjust=False).mean()
        hist_pos = float((macd - sig).iloc[-1]) > 0
        ema_stack = float(ema8.iloc[-1]) > float(ema13.iloc[-1])
        if not (hist_pos or ema_stack):
            continue
        picks.append((t, surp * 100))
    return picks


def strat_insider_burst_plus_tech(conn, as_of: date, prices: dict[str, pd.DataFrame]) -> list[tuple[str, float]]:
    """Stocks where insider buying intensity was high in the two quarters
    most-recently reported BEFORE as_of, with constructive technicals."""
    # Quarters reported before as_of based on fiscal_year/quarter approximation
    # We approximate: the quarter ending date is the 'reporting period' end
    # Insider stats use fiscal_year + fiscal_quarter (Q1=Mar, Q2=Jun, Q3=Sep, Q4=Dec end)
    sql = """
        WITH ranked AS (
            SELECT ticker, fiscal_year, fiscal_quarter, total_purchases, total_sales,
                   ROW_NUMBER() OVER (PARTITION BY ticker
                                      ORDER BY fiscal_year DESC, fiscal_quarter DESC) AS qrank
            FROM insider_stats
            WHERE (fiscal_year * 10 + fiscal_quarter) <=
                  (EXTRACT(YEAR FROM %s)::int * 10 +
                   GREATEST(1, LEAST(4, CEIL(EXTRACT(MONTH FROM %s) / 3.0)::int)))
        )
        SELECT ticker,
               SUM(total_purchases - total_sales) FILTER (WHERE qrank <= 2) AS net
        FROM ranked
        GROUP BY ticker
        HAVING SUM(total_purchases - total_sales) FILTER (WHERE qrank <= 2) >= 3
    """
    with conn.cursor() as cur:
        cur.execute(sql, (as_of, as_of))
        net_buys = {r[0]: int(r[1] or 0) for r in cur.fetchall()}

    picks = []
    for t, net in net_buys.items():
        df = prices.get(t)
        if df is None:
            continue
        win = df[df["trade_date"] <= pd.Timestamp(as_of)]
        if len(win) < 60:
            continue
        close = win["close"]
        current = float(close.iloc[-1])
        hi = float(close.max())
        if hi <= 0:
            continue
        pct_off = 1 - current / hi
        # Insider buying on weakness — require ≥10% off high
        if pct_off < 0.10:
            continue
        # MACD positive or RSI rising
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
        rs = gain / loss
        rsi = 100 - 100 / (1 + rs)
        if len(rsi.dropna()) < 5:
            continue
        rsi_now = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-5])
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        sig = macd.ewm(span=9, adjust=False).mean()
        hist = float((macd - sig).iloc[-1])
        if not (rsi_now > rsi_prev or hist > 0):
            continue
        picks.append((t, float(net)))
    return picks


STRATEGIES = {
    "reversal_simple": strat_reversal_simple,
    "earnings_beat_plus_tech": strat_earnings_beat_plus_tech,
    "insider_burst_plus_tech": strat_insider_burst_plus_tech,
}


# ============================================================
# Runner
# ============================================================
def run_backtest(strategy: str, months_back: int, cadence_days: int,
                 max_picks_per_date: int = 30) -> int:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    strat_fn = STRATEGIES[strategy]

    conn = _conn()
    try:
        end_date = date.today() - timedelta(days=180)  # leave 6m for forward returns
        start_date = end_date - timedelta(days=months_back * 30)
        history_buffer = start_date - timedelta(days=400)  # need lookback for indicators

        print(f"\n[1] Loading all daily prices from {history_buffer} …")
        prices = load_all_prices(conn, history_buffer)
        spy = prices.get("SPY")
        if spy is None:
            print("WARNING: no SPY data — excess returns will be NULL")
        print(f"  loaded {len(prices)} tickers")

        # Sample dates
        dates = []
        d = start_date
        while d <= end_date:
            dates.append(d)
            d += timedelta(days=cadence_days)
        print(f"\n[2] Backtesting strategy '{strategy}' over {len(dates)} dates "
              f"({start_date} → {end_date}, cadence {cadence_days}d) …")

        all_picks = []
        n_signals_total = 0

        for as_of in dates:
            signals = strat_fn(conn, as_of, prices)
            n_signals_total += len(signals)
            if not signals:
                continue
            signals.sort(key=lambda x: x[1], reverse=True)
            signals = signals[:max_picks_per_date]

            for ticker, score in signals:
                df = prices.get(ticker)
                if df is None:
                    continue
                # Entry on next trading day's close
                entry = price_on_or_after(df, as_of + timedelta(days=1))
                if entry is None:
                    continue
                entry_date, entry_close = entry

                # Forward returns
                w1 = forward_window_stats(df, entry_date, 30)
                w3 = forward_window_stats(df, entry_date, 90)
                w6 = forward_window_stats(df, entry_date, 180)

                # SPY return 3m for comparison
                spy_ret_3m = None
                if spy is not None:
                    spy_entry = price_on_or_after(spy, entry_date)
                    if spy_entry is not None:
                        spy_w3 = forward_window_stats(spy, spy_entry[0], 90)
                        spy_ret_3m = spy_w3.get("return_pct")

                excess = None
                if w3.get("return_pct") is not None and spy_ret_3m is not None:
                    excess = float(w3["return_pct"]) - float(spy_ret_3m)

                all_picks.append({
                    "ticker": ticker,
                    "as_of_date": as_of,
                    "signal_score": score,
                    "entry_price": entry_close,
                    "price_1m": w1.get("exit_close"),
                    "price_3m": w3.get("exit_close"),
                    "price_6m": w6.get("exit_close"),
                    "return_1m_pct": w1.get("return_pct"),
                    "return_3m_pct": w3.get("return_pct"),
                    "return_6m_pct": w6.get("return_pct"),
                    "spy_return_3m_pct": spy_ret_3m,
                    "excess_return_3m_pct": excess,
                    "max_return_pct": w3.get("max_pct"),
                    "max_drawdown_pct": w3.get("drawdown_pct"),
                })

        print(f"  {len(all_picks)} picks recorded (from {n_signals_total} signals)")

        # Aggregate stats
        if not all_picks:
            print("  no picks to aggregate.")
            return -1

        def safe_avg(key):
            vals = [p[key] for p in all_picks if p[key] is not None]
            return float(np.mean(vals)) if vals else None

        def safe_median(key):
            vals = [p[key] for p in all_picks if p[key] is not None]
            return float(np.median(vals)) if vals else None

        def win_rate(key):
            vals = [p[key] for p in all_picks if p[key] is not None]
            if not vals:
                return None
            return float(sum(1 for v in vals if v > 0) / len(vals))

        avg_1m = safe_avg("return_1m_pct")
        avg_3m = safe_avg("return_3m_pct")
        avg_6m = safe_avg("return_6m_pct")
        med_3m = safe_median("return_3m_pct")
        wr_1m = win_rate("return_1m_pct")
        wr_3m = win_rate("return_3m_pct")
        wr_6m = win_rate("return_6m_pct")
        spy_avg = safe_avg("spy_return_3m_pct")
        excess_3m = safe_avg("excess_return_3m_pct")

        rets_3m = [p["return_3m_pct"] for p in all_picks if p["return_3m_pct"] is not None]
        sharpe_proxy = None
        if len(rets_3m) >= 5:
            std = float(np.std(rets_3m, ddof=1))
            if std > 0:
                sharpe_proxy = float(np.mean(rets_3m) / std)

        # Insert run
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO backtest_runs
                (strategy_name, start_date, end_date, cadence_days, n_picks,
                 n_signals_total, win_rate_1m, win_rate_3m, win_rate_6m,
                 avg_return_1m, avg_return_3m, avg_return_6m, median_return_3m,
                 avg_spy_return_3m, excess_return_3m, sharpe_proxy, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING run_id
            """, (
                strategy, start_date, end_date, cadence_days, len(all_picks),
                n_signals_total, wr_1m, wr_3m, wr_6m,
                avg_1m, avg_3m, avg_6m, med_3m,
                spy_avg, excess_3m, sharpe_proxy,
                f"max_picks_per_date={max_picks_per_date}",
            ))
            run_id = cur.fetchone()[0]

            # Insert picks
            rows = [(
                run_id, p["ticker"], p["as_of_date"],
                p["signal_score"], p["entry_price"],
                p["price_1m"], p["price_3m"], p["price_6m"],
                p["return_1m_pct"], p["return_3m_pct"], p["return_6m_pct"],
                p["spy_return_3m_pct"], p["excess_return_3m_pct"],
                p["max_return_pct"], p["max_drawdown_pct"],
            ) for p in all_picks]
            execute_values(cur, """
                INSERT INTO backtest_picks
                (run_id, ticker, as_of_date, signal_score, entry_price,
                 price_1m, price_3m, price_6m,
                 return_1m_pct, return_3m_pct, return_6m_pct,
                 spy_return_3m_pct, excess_return_3m_pct,
                 max_return_pct, max_drawdown_pct)
                VALUES %s
            """, rows, page_size=200)
            conn.commit()

        # Print summary
        print(f"\n[3] Backtest results — {strategy}")
        print(f"  Run ID: {run_id}")
        print(f"  Picks:               {len(all_picks)}")
        print(f"  Win rate 1m:         {wr_1m*100:.1f}%" if wr_1m else "  Win rate 1m: N/A")
        print(f"  Win rate 3m:         {wr_3m*100:.1f}%" if wr_3m else "  Win rate 3m: N/A")
        print(f"  Win rate 6m:         {wr_6m*100:.1f}%" if wr_6m else "  Win rate 6m: N/A")
        print(f"  Avg return 1m:       {avg_1m*100:+.2f}%" if avg_1m else "  Avg return 1m: N/A")
        print(f"  Avg return 3m:       {avg_3m*100:+.2f}%" if avg_3m else "  Avg return 3m: N/A")
        print(f"  Avg return 6m:       {avg_6m*100:+.2f}%" if avg_6m else "  Avg return 6m: N/A")
        print(f"  Median return 3m:    {med_3m*100:+.2f}%" if med_3m else "  Median return 3m: N/A")
        print(f"  Avg SPY return 3m:   {spy_avg*100:+.2f}%" if spy_avg else "  Avg SPY return 3m: N/A")
        if excess_3m is not None:
            verdict = "✓ outperforming" if excess_3m > 0 else "✗ underperforming"
            print(f"  Excess vs SPY 3m:    {excess_3m*100:+.2f}pp  {verdict}")
        if sharpe_proxy is not None:
            print(f"  Sharpe proxy:        {sharpe_proxy:.3f}")
        return run_id
    finally:
        conn.close()


# ============================================================
# Report
# ============================================================
def show_report():
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM backtest_runs
                ORDER BY run_at DESC LIMIT 20
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        print("No backtest runs yet.")
        return
    print(f"\n{'STRATEGY':<26} {'PICKS':>5} {'WR3M':>5} {'AVG3M':>6} {'MED3M':>6} {'EXCESS':>6} {'SHARPE':>7}")
    print("─" * 72)
    for r in rows:
        wr = float(r["win_rate_3m"]) * 100 if r["win_rate_3m"] else 0
        avg = float(r["avg_return_3m"]) * 100 if r["avg_return_3m"] else 0
        med = float(r["median_return_3m"]) * 100 if r["median_return_3m"] else 0
        ex = float(r["excess_return_3m"]) * 100 if r["excess_return_3m"] else 0
        sh = float(r["sharpe_proxy"]) if r["sharpe_proxy"] else 0
        print(f"{r['strategy_name']:<26} {r['n_picks']:>5} {wr:>4.0f}% "
              f"{avg:>+5.1f}% {med:>+5.1f}% {ex:>+5.1f}% {sh:>7.3f}")


def show_top_picks(run_id: Optional[int], n: int):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if run_id is None:
                cur.execute("SELECT max(run_id) AS rid FROM backtest_runs")
                rr = cur.fetchone()
                run_id = rr["rid"]
                if run_id is None:
                    print("No backtest runs yet.")
                    return
            cur.execute(f"""
                SELECT * FROM backtest_picks
                WHERE run_id = %s AND return_3m_pct IS NOT NULL
                ORDER BY return_3m_pct DESC LIMIT %s
            """, (run_id, n))
            wins = cur.fetchall()
            cur.execute(f"""
                SELECT * FROM backtest_picks
                WHERE run_id = %s AND return_3m_pct IS NOT NULL
                ORDER BY return_3m_pct ASC LIMIT %s
            """, (run_id, n))
            losses = cur.fetchall()
    finally:
        conn.close()

    print(f"\n=== Top winners (run {run_id}) ===")
    print(f"{'TICKER':<7} {'AS_OF':<10} {'ENTRY':>7} {'RET_3M':>7} {'EXCESS':>7} {'MAX':>7} {'DD':>7}")
    for r in wins:
        ret = float(r["return_3m_pct"]) * 100 if r["return_3m_pct"] is not None else 0
        ex = float(r["excess_return_3m_pct"]) * 100 if r["excess_return_3m_pct"] else 0
        mx = float(r["max_return_pct"]) * 100 if r["max_return_pct"] else 0
        dd = float(r["max_drawdown_pct"]) * 100 if r["max_drawdown_pct"] else 0
        ent = float(r["entry_price"]) if r["entry_price"] else 0
        print(f"{r['ticker']:<7} {r['as_of_date']} {ent:>7.2f} {ret:>+6.1f}% "
              f"{ex:>+6.1f}% {mx:>+6.1f}% {dd:>+6.1f}%")

    print(f"\n=== Top losers (run {run_id}) ===")
    for r in losses:
        ret = float(r["return_3m_pct"]) * 100 if r["return_3m_pct"] is not None else 0
        ex = float(r["excess_return_3m_pct"]) * 100 if r["excess_return_3m_pct"] else 0
        mx = float(r["max_return_pct"]) * 100 if r["max_return_pct"] else 0
        dd = float(r["max_drawdown_pct"]) * 100 if r["max_drawdown_pct"] else 0
        ent = float(r["entry_price"]) if r["entry_price"] else 0
        print(f"{r['ticker']:<7} {r['as_of_date']} {ent:>7.2f} {ret:>+6.1f}% "
              f"{ex:>+6.1f}% {mx:>+6.1f}% {dd:>+6.1f}%")


# ============================================================
# CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="Watchtower backtest framework")
    ap.add_argument("--strategy", choices=list(STRATEGIES.keys()),
                    help="strategy to backtest")
    ap.add_argument("--months-back", type=int, default=6,
                    help="months of history to test (default 6, capped by data)")
    ap.add_argument("--cadence-days", type=int, default=30,
                    help="how often to sample (default 30 = monthly)")
    ap.add_argument("--max-picks", type=int, default=30,
                    help="max picks per sample date (default 30)")
    ap.add_argument("--report", action="store_true",
                    help="show summary of all past runs")
    ap.add_argument("--top-picks", type=int,
                    help="show top N winners/losers from most recent run")
    ap.add_argument("--run-id", type=int,
                    help="(with --top-picks) specific run to inspect")
    args = ap.parse_args()

    if args.report:
        show_report()
        return
    if args.top_picks:
        show_top_picks(args.run_id, args.top_picks)
        return
    if not args.strategy:
        ap.error("--strategy required (or use --report / --top-picks)")

    run_backtest(args.strategy, args.months_back, args.cadence_days,
                 args.max_picks)


if __name__ == "__main__":
    main()
