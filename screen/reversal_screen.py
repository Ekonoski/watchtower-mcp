"""
Watchtower — Reversal screen for beaten-down quality stocks (GMMSS Sleeve 1).

Finds stocks that:
  1. Passed the Compounder quality screen (strong fundamentals)
  2. Are significantly off their 52-week highs (beaten down)
  3. Show technical reversal signals (ready to turn)

Also serves as the shared base module for momentum_screen and breakdown_screen,
exporting: _conn, load_quality_tickers, load_prices, compute_rsi, compute_ema,
compute_macd, resample_weekly, compute_volume_ratio, compute_volume_surge,
compute_spy_regime, and EMA/RSI constants.

Usage:
    set -a && source .env && set +a
    python3 screen/reversal_screen.py
    python3 screen/reversal_screen.py --min-drawdown 25
    python3 screen/reversal_screen.py --ticker LULU
    python3 screen/reversal_screen.py --all
"""
import argparse
import os
import socket
import subprocess
import sys
import time
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
    import pandas as pd
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError as e:
    print(f"ERROR: missing dependency: {e}.  Run:  pip install pandas numpy psycopg2-binary",
          file=sys.stderr)
    sys.exit(1)


# ── Technical parameters ──────────────────────────────────────────────────────
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
EMA_SHORT = 8
EMA_LONG = 13
EMA_TREND = 50
VOLUME_LOOKBACK = 20
RSI_SLOPE_WINDOW = 5
DIVERGENCE_WINDOW = 40

# Reversal scoring weights (sum to 1.0)
W_RSI_RECOVERY = 0.20
W_RSI_DIVERGENCE = 0.15
W_MACD = 0.20
W_EMA_CROSS = 0.20
W_VOLUME = 0.10
W_PRICE_VS_EMA = 0.15


# ── DNS / DB connection ───────────────────────────────────────────────────────
_IPV6_CACHE: dict = {}


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
        raise RuntimeError(f"Missing env vars: {missing}")
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
        if not hostaddr:
            raise RuntimeError(f"DNS lookup failed for {host}")
    last_err: Optional[Exception] = None
    for attempt in range(4):
        try:
            # TCP keepalives: without them a half-dead pooler connection
            # (server gone, no RST) blocks recv() forever — statement_timeout
            # can't save us because the cancel never reaches a dead server.
            # With these, a dead peer is detected in ~60s and raises.
            kwargs = dict(host=host, port=port, user=user, password=password,
                          dbname=dbname, sslmode="require", connect_timeout=15,
                          keepalives=1, keepalives_idle=30,
                          keepalives_interval=10, keepalives_count=3)
            if hostaddr:
                kwargs["hostaddr"] = hostaddr
            conn = psycopg2.connect(**kwargs)
            with conn.cursor() as c:
                c.execute("SET statement_timeout = '120s'")
            conn.commit()
            return conn
        except psycopg2.OperationalError as e:
            last_err = e
            msg = str(e)
            if any(kw in msg for kw in ("could not translate", "unreachable", "timeout")):
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"Could not connect after 4 attempts: {last_err}")


# ── Data loading ──────────────────────────────────────────────────────────────

def load_quality_tickers(conn, broad: bool = False) -> Dict[str, dict]:
    """Load latest quality_universe snapshot. broad=True includes all tickers."""
    sql = """
        SELECT q.ticker, t.company_name, t.sector, t.industry,
               q.roic_5yr_avg, q.revenue_growth_3yr, q.fcf_positive_years,
               q.net_debt_to_ebitda, q.gross_margin
        FROM quality_universe q
        JOIN tickers t ON t.ticker = q.ticker
        WHERE q.as_of_date = (SELECT max(as_of_date) FROM quality_universe)
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        return {r["ticker"]: dict(r) for r in cur.fetchall()}


def load_prices(conn, tickers: List[str], days: int = 300) -> Dict[str, pd.DataFrame]:
    """Load daily close/volume into per-ticker DataFrames."""
    if not tickers:
        return {}
    sql = """
        SELECT ticker, trade_date, close, volume
        FROM daily_prices
        WHERE ticker = ANY(%(tickers)s)
          AND trade_date >= current_date - %(days)s * interval '1 day'
        ORDER BY ticker, trade_date
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, {"tickers": tickers, "days": days})
        rows = cur.fetchall()
    frames: Dict[str, list] = {}
    for r in rows:
        frames.setdefault(r["ticker"], []).append(r)
    result: Dict[str, pd.DataFrame] = {}
    for t, data in frames.items():
        df = pd.DataFrame(data)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        result[t] = df
    return result


# ── Indicator computation ─────────────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_macd(close: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = compute_ema(close, MACD_FAST)
    ema_slow = compute_ema(close, MACD_SLOW)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, MACD_SIGNAL)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly (Friday close)."""
    if df.empty or "trade_date" not in df.columns:
        return pd.DataFrame(columns=["close", "volume"])
    tmp = df.set_index("trade_date")
    weekly = tmp["close"].resample("W-FRI").last().dropna()
    vol_weekly = tmp["volume"].resample("W-FRI").sum()
    wdf = pd.DataFrame({"close": weekly, "volume": vol_weekly}).dropna()
    return wdf.reset_index(drop=True)


def compute_volume_ratio(df: pd.DataFrame, lookback: int = VOLUME_LOOKBACK) -> float:
    """Ratio of average up-day volume to average down-day volume."""
    if len(df) < lookback + 1:
        return 1.0
    recent = df.tail(lookback + 1).copy()
    recent["change"] = recent["close"].diff()
    recent = recent.iloc[1:]
    up_vol = recent.loc[recent["change"] > 0, "volume"]
    dn_vol = recent.loc[recent["change"] < 0, "volume"]
    avg_up = up_vol.mean() if len(up_vol) > 0 else 0
    avg_dn = dn_vol.mean() if len(dn_vol) > 0 else 1
    return avg_up / avg_dn if avg_dn > 0 else 2.0


def compute_volume_surge(df: pd.DataFrame, lookback: int = 20, recent: int = 5) -> float:
    """Ratio of recent avg volume to longer-term avg volume (surge indicator)."""
    if len(df) < lookback + recent:
        return 1.0
    vol = df["volume"]
    recent_avg = vol.iloc[-recent:].mean()
    baseline_avg = vol.iloc[-(lookback + recent):-recent].mean()
    if baseline_avg <= 0:
        return 1.0
    return recent_avg / baseline_avg


def compute_spy_regime(spy_df: pd.DataFrame, ema_period: int = 200) -> Optional[bool]:
    """Return True (bull) if SPY is above its 200-day EMA, False (bear), or None."""
    if spy_df is None or len(spy_df) < ema_period:
        return None
    ema200 = compute_ema(spy_df["close"], ema_period)
    return bool(spy_df["close"].iloc[-1] > ema200.iloc[-1])


def detect_bullish_divergence(close: pd.Series, rsi: pd.Series,
                               window: int = DIVERGENCE_WINDOW) -> bool:
    if len(close) < window * 2:
        return False
    recent = slice(-window, None)
    prior = slice(-window * 2, -window)
    recent_price_low = close.iloc[recent].min()
    prior_price_low = close.iloc[prior].min()
    recent_low_idx = close.iloc[recent].idxmin()
    prior_low_idx = close.iloc[prior].idxmin()
    if recent_low_idx not in rsi.index or prior_low_idx not in rsi.index:
        return False
    return (recent_price_low < prior_price_low and
            rsi.loc[recent_low_idx] > rsi.loc[prior_low_idx])


# ── Reversal scoring ──────────────────────────────────────────────────────────

def score_rsi_recovery(rsi_current: float, rsi_slope: float) -> float:
    if np.isnan(rsi_current) or np.isnan(rsi_slope):
        return 0.0
    if rsi_current < 30:
        zone = 0.8 if rsi_slope > 0 else 0.3
    elif rsi_current < 40:
        zone = 1.0 if rsi_slope > 0 else 0.5
    elif rsi_current < 50:
        zone = 0.7 if rsi_slope > 0 else 0.3
    elif rsi_current < 60:
        zone = 0.4
    else:
        zone = 0.1
    slope_bonus = min(0.2, max(0.0, rsi_slope * 0.05)) if rsi_slope > 0 else 0.0
    return min(1.0, zone + slope_bonus)


def score_rsi_divergence(has_divergence: bool) -> float:
    return 1.0 if has_divergence else 0.0


def score_macd(hist_current: float, hist_prev: float) -> float:
    if np.isnan(hist_current) or np.isnan(hist_prev):
        return 0.0
    if hist_current > 0 and hist_prev <= 0:
        return 1.0
    if hist_current > 0 and hist_current > hist_prev:
        return 0.8
    if hist_current > 0:
        return 0.5
    if hist_current > hist_prev:
        return 0.4
    return 0.0


def score_ema_crossover(ema_short_val: float, ema_long_val: float,
                        ema_short_prev: float, ema_long_prev: float) -> float:
    if any(np.isnan(v) for v in [ema_short_val, ema_long_val, ema_short_prev, ema_long_prev]):
        return 0.0
    above_now = ema_short_val > ema_long_val
    above_prev = ema_short_prev > ema_long_prev
    if above_now and not above_prev:
        return 1.0
    if above_now:
        return 0.7
    gap_pct = (ema_short_val - ema_long_val) / ema_long_val if ema_long_val != 0 else 0
    if gap_pct > -0.01:
        return 0.4
    if gap_pct > -0.03:
        return 0.2
    return 0.0


def score_volume_accumulation(vol_ratio: float) -> float:
    if vol_ratio >= 1.8:
        return 1.0
    if vol_ratio >= 1.3:
        return 0.7
    if vol_ratio >= 1.0:
        return 0.4
    return 0.1


def score_price_vs_ema(close: float, ema50: float) -> float:
    if np.isnan(close) or np.isnan(ema50) or ema50 == 0:
        return 0.0
    pct = (close - ema50) / ema50
    if pct > 0.02:
        return 1.0
    if pct > -0.02:
        return 0.8
    if pct > -0.05:
        return 0.5
    if pct > -0.10:
        return 0.2
    return 0.0


# ── Polygon snapshot refresh ─────────────────────────────────────────────────

def _patch_polygon_snapshots(frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Refresh today's close/volume for all tickers in one Polygon snapshot call.

    Gracefully returns frames unchanged if Polygon key is missing or any error occurs.
    """
    try:
        from analysis.polygon_data import get_client
        client = get_client()
        if client is None:
            return frames

        tickers_req = ",".join(list(frames.keys())[:250])
        snaps = client.get_snapshot_all("stocks", params={"tickers": tickers_req})
        today = pd.Timestamp(date.today())

        for snap in (snaps or []):
            try:
                t = snap.ticker
                if t not in frames:
                    continue
                day = getattr(snap, "day", None)
                if day is None:
                    continue
                close_val = getattr(day, "c", None)
                if not close_val:
                    continue
                df = frames[t]
                if not df.empty and df["trade_date"].iloc[-1] >= today:
                    continue
                vol_val = float(getattr(day, "v", None) or df["volume"].iloc[-1])
                new_row = pd.DataFrame([{
                    "trade_date": today,
                    "close": float(close_val),
                    "volume": vol_val,
                }])
                frames[t] = pd.concat([df, new_row], ignore_index=True)
            except Exception:
                continue
    except Exception:
        pass
    return frames


# ── Main analysis ─────────────────────────────────────────────────────────────

def analyze_ticker(df: pd.DataFrame) -> Optional[dict]:
    if len(df) < 60:
        return None
    close = df["close"]
    rsi = compute_rsi(close)
    _, _, histogram = compute_macd(close)
    ema_short = compute_ema(close, EMA_SHORT)
    ema_long = compute_ema(close, EMA_LONG)
    ema_trend = compute_ema(close, EMA_TREND)

    rsi_current = rsi.iloc[-1]
    rsi_prev5 = rsi.iloc[-RSI_SLOPE_WINDOW - 1] if len(rsi) > RSI_SLOPE_WINDOW else rsi.iloc[0]
    rsi_slope = (rsi_current - rsi_prev5) / RSI_SLOPE_WINDOW

    hi_52w = close.max()
    current_close = close.iloc[-1]
    pct_off_high = (1 - current_close / hi_52w) * 100 if hi_52w > 0 else 0

    has_divergence = detect_bullish_divergence(close, rsi)
    vol_ratio = compute_volume_ratio(df)
    vol_surge = compute_volume_surge(df)

    s_rsi = score_rsi_recovery(rsi_current, rsi_slope)
    s_div = score_rsi_divergence(has_divergence)
    s_macd = score_macd(histogram.iloc[-1], histogram.iloc[-2])
    s_ema = score_ema_crossover(ema_short.iloc[-1], ema_long.iloc[-1],
                                ema_short.iloc[-6], ema_long.iloc[-6])
    s_vol = score_volume_accumulation(vol_ratio)
    s_price = score_price_vs_ema(current_close, ema_trend.iloc[-1])

    composite = (
        W_RSI_RECOVERY * s_rsi +
        W_RSI_DIVERGENCE * s_div +
        W_MACD * s_macd +
        W_EMA_CROSS * s_ema +
        W_VOLUME * s_vol +
        W_PRICE_VS_EMA * s_price
    ) * 100

    if composite >= 70:
        signal = "STRONG BUY"
    elif composite >= 55:
        signal = "BUY"
    elif composite >= 40:
        signal = "WATCH"
    else:
        signal = "WAIT"

    return {
        "current_price": current_close,
        "high_52w": hi_52w,
        "pct_off_high": pct_off_high,
        "rsi": rsi_current,
        "rsi_slope": rsi_slope,
        "rsi_divergence": has_divergence,
        "macd_hist": histogram.iloc[-1],
        "macd_hist_prev": histogram.iloc[-2],
        "ema_short": ema_short.iloc[-1],
        "ema_long": ema_long.iloc[-1],
        "ema_50": ema_trend.iloc[-1],
        "vol_ratio": vol_ratio,
        "vol_surge": vol_surge,
        "s_rsi": s_rsi,
        "s_div": s_div,
        "s_macd": s_macd,
        "s_ema": s_ema,
        "s_vol": s_vol,
        "s_price": s_price,
        "reversal_score": composite,
        "signal": signal,
    }


def run_screen(min_drawdown: float = 15.0, single_ticker: str = None,
               show_all: bool = False, broad: bool = False,
               with_plan: bool = False, account_equity: float = 100000.0,
               risk_pct: float = 1.0, atr_multiplier: float = 2.0) -> List[dict]:
    conn = _conn()
    try:
        quality = load_quality_tickers(conn, broad=broad)
        if single_ticker:
            tickers = [single_ticker.upper()]
        else:
            tickers = list(quality.keys())
        load_tickers = list(set(tickers + ["SPY"]))
        prices = load_prices(conn, load_tickers)
    finally:
        conn.close()

    prices = _patch_polygon_snapshots(prices)

    spy_regime = None
    if "SPY" in prices:
        spy_regime = compute_spy_regime(prices["SPY"])

    results = []
    for t in tickers:
        df = prices.get(t)
        if df is None or df.empty:
            continue
        analysis = analyze_ticker(df)
        if analysis is None:
            continue

        # Update vol_surge from already-patched prices DataFrame
        analysis["vol_surge"] = compute_volume_surge(df)

        fundamentals = quality.get(t, {})

        # RS vs SPY
        rs_vs_spy = None
        spy_df = prices.get("SPY")
        if spy_df is not None and len(df) > 20 and len(spy_df) > 20:
            min_len = min(40, len(df), len(spy_df))
            t_ret = (df["close"].iloc[-1] / df["close"].iloc[-min_len] - 1) if df["close"].iloc[-min_len] != 0 else 0
            s_ret = (spy_df["close"].iloc[-1] / spy_df["close"].iloc[-min_len] - 1) if spy_df["close"].iloc[-min_len] != 0 else 0
            if s_ret != 0:
                rs_vs_spy = t_ret / s_ret

        # Regime/RS boosts
        rscore = analysis.get("reversal_score", 0.0)
        if rs_vs_spy is not None and rs_vs_spy > 0.8:
            rscore = min(100.0, rscore * 1.03)
        if spy_regime is True:
            rscore = min(100.0, rscore * 1.02)
        analysis["reversal_score"] = rscore

        row = {"ticker": t, "sleeve": "reversal", **fundamentals, **analysis}
        if spy_regime is not None:
            row["spy_regime"] = spy_regime
        if rs_vs_spy is not None:
            row["rs_vs_spy"] = rs_vs_spy

        if show_all or single_ticker or analysis["pct_off_high"] >= min_drawdown:
            if with_plan:
                try:
                    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    from signals.trade_plan import compute_trade_plan
                    row["plan"] = compute_trade_plan(
                        close=df["close"],
                        current_price=analysis["current_price"],
                        account_equity=account_equity,
                        risk_per_trade_pct=risk_pct,
                        atr_multiplier=atr_multiplier,
                        conviction_score=analysis["reversal_score"],
                    )
                except Exception:
                    row["plan"] = None
            results.append(row)

    results.sort(key=lambda r: r["reversal_score"], reverse=True)
    return results


# ── Output ────────────────────────────────────────────────────────────────────

def print_summary(results: List[dict], min_drawdown: float):
    print(f"\n{'=' * 100}")
    print(f"  REVERSAL SCREEN — Beaten-Down Quality Ready to Turn (GMMSS Sleeve 1)")
    print(f"  {date.today().isoformat()}  |  Filter: ≥{min_drawdown:.0f}% off 52w high  |  {len(results)} candidates")
    print(f"{'=' * 100}")
    if not results:
        print("\n  No candidates found.\n")
        return
    print(f"\n  {'TICKER':<7} {'COMPANY':<25} {'SECTOR':<20} {'PRICE':>8} {'52wHI':>8} "
          f"{'%OFF':>6} {'SCORE':>6} {'RSI':>5} {'MACD':>6} {'SIGNAL':<11}")
    print(f"  {'─'*7} {'─'*25} {'─'*20} {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*5} {'─'*6} {'─'*11}")
    for r in results:
        macd_dir = "+" if r["macd_hist"] > 0 else "−"
        rsi_dir = "↑" if r.get("rsi_slope", 0) > 0 else "↓"
        print(f"  {r['ticker']:<7} {(r.get('company_name') or '')[:24]:<25} "
              f"{(r.get('sector') or '')[:19]:<20} "
              f"{r['current_price']:>8.2f} {r['high_52w']:>8.2f} "
              f"{r['pct_off_high']:>5.1f}% {r['reversal_score']:>5.0f} "
              f" {rsi_dir}{r['rsi']:>3.0f}   {macd_dir}{abs(r['macd_hist']):>4.2f} {r['signal']:<11}")


def print_detail(r: dict):
    print(f"\n{'=' * 70}")
    print(f"  {r['ticker']} — {r.get('company_name', 'N/A')}")
    print(f"  {r.get('sector', '')} / {r.get('industry', '')}")
    print(f"{'=' * 70}")
    print(f"\n  PRICE")
    print(f"    Current:     ${r['current_price']:.2f}")
    print(f"    52w High:    ${r['high_52w']:.2f}")
    print(f"    Drawdown:    {r['pct_off_high']:.1f}%")
    print(f"\n  RSI: {r['rsi']:.1f}  Divergence: {'YES' if r['rsi_divergence'] else 'no'}")
    print(f"  MACD hist: {r['macd_hist']:+.3f}  EMA 8/13: {'8>13 ✓' if r['ema_short'] > r['ema_long'] else '8<13'}")
    print(f"  Vol ratio: {r['vol_ratio']:.2f}x  Score: {r['reversal_score']:.0f} / 100  → {r['signal']}")


def main():
    ap = argparse.ArgumentParser(description="Reversal screen — beaten-down quality stocks")
    ap.add_argument("--min-drawdown", type=float, default=15.0)
    ap.add_argument("--ticker", help="single ticker detail")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--broad", action="store_true")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--with-plan", action="store_true")
    ap.add_argument("--equity", type=float, default=100000)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--atr-mult", type=float, default=2.0)
    args = ap.parse_args()

    results = run_screen(
        min_drawdown=args.min_drawdown,
        single_ticker=args.ticker,
        show_all=args.all,
        broad=args.broad,
        with_plan=args.with_plan,
        account_equity=args.equity,
        risk_pct=args.risk_pct,
        atr_multiplier=args.atr_mult,
    )

    if args.ticker and results:
        print_detail(results[0])
    else:
        print_summary(results[:args.top], args.min_drawdown)


if __name__ == "__main__":
    main()
