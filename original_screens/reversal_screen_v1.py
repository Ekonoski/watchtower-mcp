"""
Watchtower — Reversal screen for beaten-down quality stocks.

Finds stocks that:
  1. Passed the Compounder quality screen (strong fundamentals)
  2. Are significantly off their 52-week highs (beaten down)
  3. Show technical reversal signals (ready to turn)

Technical indicators computed from daily_prices:
  - RSI (14): recovering from oversold, bullish divergence
  - MACD (12,26,9): histogram turning positive
  - EMA crossover (10/21): short-term trend change
  - Volume accumulation: institutional buying patterns
  - Price vs 50 EMA: reclaiming key level

Each component produces a 0-1 sub-score; weighted composite = reversal_score.
Higher score = stronger evidence of bottoming / early reversal.

Data: daily_prices (close, volume) + quality_universe (fundamentals).

Usage:
    set -a && source .env && set +a
    python3 screen/reversal_screen.py                    # default: ≥15% off high
    python3 screen/reversal_screen.py --min-drawdown 25  # ≥25% off high
    python3 screen/reversal_screen.py --ticker LULU      # single ticker detail
    python3 screen/reversal_screen.py --all              # show all quality names
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
    print(f"ERROR: missing dependency: {e}.  Run:  python3 -m pip install --user "
          f"pandas numpy psycopg2-binary", file=sys.stderr)
    sys.exit(1)


# ============================================================
# Technical parameters
# ============================================================
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

# Scoring weights (sum to 1.0)
W_RSI_RECOVERY = 0.20
W_RSI_DIVERGENCE = 0.15
W_MACD = 0.20
W_EMA_CROSS = 0.20
W_VOLUME = 0.10
W_PRICE_VS_EMA = 0.15


# ============================================================
# DNS / connection (same pattern as compounder.py)
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
        raise RuntimeError(f"missing env vars: {missing}.  Run: set -a && source .env && set +a")
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
            kwargs = dict(host=host, port=port, user=user, password=password,
                          dbname=dbname, sslmode="require", connect_timeout=15)
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
            if "could not translate" in msg or "unreachable" in msg or "timeout" in msg:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"could not connect after 4 attempts: {last_err}")


# ============================================================
# Data loading
# ============================================================
def load_quality_tickers(conn) -> Dict[str, dict]:
    """Load the latest quality_universe snapshot with fundamentals metrics."""
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
    """Load daily close/volume from daily_prices into per-ticker DataFrames."""
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
    frames: Dict[str, pd.DataFrame] = {}
    for r in rows:
        frames.setdefault(r["ticker"], []).append(r)
    for t, data in frames.items():
        df = pd.DataFrame(data)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        frames[t] = df
    return frames


# ============================================================
# Technical indicator computation
# ============================================================
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


def detect_bullish_divergence(close: pd.Series, rsi: pd.Series, window: int = DIVERGENCE_WINDOW) -> bool:
    """Detect bullish divergence: price making lower lows while RSI makes higher lows."""
    if len(close) < window + 10:
        return False
    recent = slice(-window, None)
    prior = slice(-window * 2, -window)
    if len(close) < window * 2:
        return False
    recent_price_low = close.iloc[recent].min()
    prior_price_low = close.iloc[prior].min()
    recent_low_idx = close.iloc[recent].idxmin()
    prior_low_idx = close.iloc[prior].idxmin()
    if recent_low_idx not in rsi.index or prior_low_idx not in rsi.index:
        return False
    recent_rsi_at_low = rsi.loc[recent_low_idx]
    prior_rsi_at_low = rsi.loc[prior_low_idx]
    return recent_price_low < prior_price_low and recent_rsi_at_low > prior_rsi_at_low


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
    if avg_dn == 0:
        return 2.0
    return avg_up / avg_dn


# ============================================================
# Scoring
# ============================================================
def score_rsi_recovery(rsi_current: float, rsi_slope: float) -> float:
    """Score 0-1: reward RSI recovering from oversold zone."""
    if np.isnan(rsi_current) or np.isnan(rsi_slope):
        return 0.0
    zone = 0.0
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
    """Score 0-1: reward MACD histogram turning positive."""
    if np.isnan(hist_current) or np.isnan(hist_prev):
        return 0.0
    if hist_current > 0 and hist_prev <= 0:
        return 1.0  # fresh crossover
    if hist_current > 0 and hist_current > hist_prev:
        return 0.8  # positive and rising
    if hist_current > 0:
        return 0.5  # positive but fading
    if hist_current > hist_prev:
        return 0.4  # negative but improving
    return 0.0


def score_ema_crossover(ema_short_val: float, ema_long_val: float,
                        ema_short_prev: float, ema_long_prev: float) -> float:
    """Score 0-1: reward short EMA crossing above long EMA."""
    if any(np.isnan(v) for v in [ema_short_val, ema_long_val, ema_short_prev, ema_long_prev]):
        return 0.0
    above_now = ema_short_val > ema_long_val
    above_prev = ema_short_prev > ema_long_prev
    if above_now and not above_prev:
        return 1.0  # fresh crossover
    if above_now:
        return 0.7  # already crossed, trending
    gap_pct = (ema_short_val - ema_long_val) / ema_long_val
    if gap_pct > -0.01:
        return 0.4  # converging, almost crossing
    if gap_pct > -0.03:
        return 0.2  # getting closer
    return 0.0


def score_volume_accumulation(vol_ratio: float) -> float:
    """Score 0-1: reward up-volume exceeding down-volume."""
    if vol_ratio >= 1.8:
        return 1.0
    if vol_ratio >= 1.3:
        return 0.7
    if vol_ratio >= 1.0:
        return 0.4
    return 0.1


def score_price_vs_ema(close: float, ema50: float) -> float:
    """Score 0-1: reward price near or above 50 EMA."""
    if np.isnan(close) or np.isnan(ema50) or ema50 == 0:
        return 0.0
    pct = (close - ema50) / ema50
    if pct > 0.02:
        return 1.0  # above
    if pct > -0.02:
        return 0.8  # right at it
    if pct > -0.05:
        return 0.5  # close below
    if pct > -0.10:
        return 0.2
    return 0.0


# ============================================================
# Main analysis
# ============================================================
def analyze_ticker(df: pd.DataFrame) -> Optional[dict]:
    """Compute all indicators and composite score for one ticker."""
    if len(df) < 60:
        return None

    close = df["close"]
    rsi = compute_rsi(close)
    macd_line, signal_line, histogram = compute_macd(close)
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

    # Signal label
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
               show_all: bool = False) -> List[dict]:
    """Run the full reversal screen. Returns sorted results."""
    conn = _conn()
    try:
        quality = load_quality_tickers(conn)
        if single_ticker:
            tickers = [single_ticker.upper()]
        else:
            tickers = list(quality.keys())
        prices = load_prices(conn, tickers)
    finally:
        conn.close()

    results = []
    for t in tickers:
        df = prices.get(t)
        if df is None or df.empty:
            continue
        analysis = analyze_ticker(df)
        if analysis is None:
            continue
        fundamentals = quality.get(t, {})
        row = {"ticker": t, **fundamentals, **analysis}
        if show_all or single_ticker or analysis["pct_off_high"] >= min_drawdown:
            results.append(row)

    results.sort(key=lambda r: r["reversal_score"], reverse=True)
    return results


# ============================================================
# Output formatting
# ============================================================
def print_summary(results: List[dict], min_drawdown: float):
    print(f"\n{'=' * 100}")
    print(f"  REVERSAL SCREEN — Beaten-Down Quality Ready to Turn")
    print(f"  {date.today().isoformat()}  |  Filter: ≥{min_drawdown:.0f}% off 52w high  |  {len(results)} candidates")
    print(f"{'=' * 100}")

    if not results:
        print("\n  No candidates found.\n")
        return

    print(f"\n  {'TICKER':<7} {'COMPANY':<25} {'SECTOR':<20} {'PRICE':>8} {'52wHI':>8} "
          f"{'%OFF':>6} {'SCORE':>6} {'RSI':>5} {'MACD':>6} {'SIGNAL':<11}")
    print(f"  {'─' * 7} {'─' * 25} {'─' * 20} {'─' * 8} {'─' * 8} "
          f"{'─' * 6} {'─' * 6} {'─' * 5} {'─' * 6} {'─' * 11}")

    for r in results:
        macd_dir = "+" if r["macd_hist"] > 0 else "−"
        rsi_dir = "↑" if r.get("rsi_slope", 0) > 0 else "↓"
        company = (r.get("company_name") or "")[:24]
        sector = (r.get("sector") or "")[:19]
        print(f"  {r['ticker']:<7} {company:<25} {sector:<20} "
              f"{r['current_price']:>8.2f} {r['high_52w']:>8.2f} "
              f"{r['pct_off_high']:>5.1f}% "
              f"{r['reversal_score']:>5.0f} "
              f" {rsi_dir}{r['rsi']:>3.0f} "
              f"  {macd_dir}{abs(r['macd_hist']):>4.2f} "
              f"{r['signal']:<11}")


def print_detail(r: dict):
    print(f"\n{'=' * 70}")
    print(f"  {r['ticker']} — {r.get('company_name', 'N/A')}")
    print(f"  {r.get('sector', '')} / {r.get('industry', '')}")
    print(f"{'=' * 70}")

    print(f"\n  PRICE")
    print(f"    Current:     ${r['current_price']:.2f}")
    print(f"    52w High:    ${r['high_52w']:.2f}")
    print(f"    Drawdown:    {r['pct_off_high']:.1f}%")

    print(f"\n  FUNDAMENTALS (Compounder quality screen)")
    roic = r.get("roic_5yr_avg")
    rev = r.get("revenue_growth_3yr")
    gm = r.get("gross_margin")
    nd = r.get("net_debt_to_ebitda")
    print(f"    ROIC 5yr avg:      {float(roic)*100:.1f}%" if roic else "    ROIC 5yr avg:      N/A")
    print(f"    Revenue growth 3yr:{float(rev)*100:.1f}%" if rev else "    Revenue growth 3yr:N/A")
    print(f"    FCF positive years:{r.get('fcf_positive_years', 'N/A')}")
    print(f"    Gross margin:      {float(gm)*100:.1f}%" if gm else "    Gross margin:      N/A")
    print(f"    Net Debt/EBITDA:   {float(nd):.2f}" if nd else "    Net Debt/EBITDA:   N/A")

    print(f"\n  TECHNICAL INDICATORS")
    rsi_dir = "↑ rising" if r.get("rsi_slope", 0) > 0 else "↓ falling"
    print(f"    RSI (14):          {r['rsi']:.1f} ({rsi_dir}, slope {r['rsi_slope']:.2f}/day)")
    print(f"    RSI divergence:    {'YES — bullish' if r['rsi_divergence'] else 'no'}")
    macd_dir = "positive" if r["macd_hist"] > 0 else "negative"
    macd_trend = "improving" if r["macd_hist"] > r["macd_hist_prev"] else "fading"
    print(f"    MACD histogram:    {r['macd_hist']:.3f} ({macd_dir}, {macd_trend})")
    cross = "ABOVE" if r["ema_short"] > r["ema_long"] else "below"
    print(f"    EMA 8/13:          8 EMA {cross} 13 EMA  (8={r['ema_short']:.2f}, 13={r['ema_long']:.2f})")
    print(f"    Volume up/dn:      {r['vol_ratio']:.2f}x  ({'accumulation' if r['vol_ratio'] > 1.2 else 'neutral' if r['vol_ratio'] > 0.8 else 'distribution'})")
    pct_from_50 = ((r["current_price"] - r["ema_50"]) / r["ema_50"]) * 100
    pos = "above" if pct_from_50 > 0 else "below"
    print(f"    Price vs 50 EMA:   {abs(pct_from_50):.1f}% {pos}  (50 EMA = {r['ema_50']:.2f})")

    print(f"\n  SCORING BREAKDOWN (0-100 composite)")
    print(f"    RSI recovery:      {r['s_rsi']:.2f} × {W_RSI_RECOVERY:.0%} = {r['s_rsi']*W_RSI_RECOVERY*100:.1f}")
    print(f"    RSI divergence:    {r['s_div']:.2f} × {W_RSI_DIVERGENCE:.0%} = {r['s_div']*W_RSI_DIVERGENCE*100:.1f}")
    print(f"    MACD momentum:     {r['s_macd']:.2f} × {W_MACD:.0%} = {r['s_macd']*W_MACD*100:.1f}")
    print(f"    EMA crossover:     {r['s_ema']:.2f} × {W_EMA_CROSS:.0%} = {r['s_ema']*W_EMA_CROSS*100:.1f}")
    print(f"    Volume accum:      {r['s_vol']:.2f} × {W_VOLUME:.0%} = {r['s_vol']*W_VOLUME*100:.1f}")
    print(f"    Price vs 50 EMA:   {r['s_price']:.2f} × {W_PRICE_VS_EMA:.0%} = {r['s_price']*W_PRICE_VS_EMA*100:.1f}")
    print(f"    ─────────────────────────────────────")
    print(f"    REVERSAL SCORE:    {r['reversal_score']:.0f} / 100  →  {r['signal']}")


def main():
    ap = argparse.ArgumentParser(description="Reversal screen for beaten-down quality stocks")
    ap.add_argument("--min-drawdown", type=float, default=15.0,
                    help="minimum %% off 52w high to include (default 15)")
    ap.add_argument("--ticker", help="show detailed analysis for a single ticker")
    ap.add_argument("--all", action="store_true", help="show all quality names regardless of drawdown")
    ap.add_argument("--top", type=int, default=25, help="show top N results (default 25)")
    args = ap.parse_args()

    results = run_screen(
        min_drawdown=args.min_drawdown,
        single_ticker=args.ticker,
        show_all=args.all,
    )

    if args.ticker and results:
        print_detail(results[0])
    else:
        print_summary(results[:args.top], args.min_drawdown)


if __name__ == "__main__":
    main()
