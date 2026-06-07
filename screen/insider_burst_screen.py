"""
Watchtower — Insider burst screen (GMMSS activity-driven sleeve).

Finds quality stocks where insiders (officers/directors) have been buying,
combined with a technical setup that confirms the thesis. The idea:
insiders know the business best — when they buy AND technicals align,
it's a higher-conviction long candidate.

Two modes:
1. DB mode: queries an insider_transactions table if it exists.
2. Fallback mode: uses unusual volume burst (vol_surge > threshold) on
   quality names as a proxy for institutional / informed buying activity.

Score = insider signal strength + technical confirmation.

Reuses reversal_screen helpers.

Usage:
    python screen/insider_burst_screen.py
    python screen/insider_burst_screen.py --top 10
    python screen/insider_burst_screen.py --days 30
    python screen/insider_burst_screen.py --ticker AAPL
"""
import argparse
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional

try:
    import numpy as np
    import pandas as pd
except ImportError as e:
    print(f"ERROR: missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from reversal_screen import (
    _conn,
    load_quality_tickers,
    load_prices,
    compute_rsi, compute_ema, compute_macd,
    compute_volume_ratio,
    compute_volume_surge,
    compute_spy_regime,
    EMA_SHORT, EMA_LONG, EMA_TREND, RSI_PERIOD,
)


def _load_insider_tickers(conn, days: int = 60) -> Dict[str, dict]:
    """Try to load recent insider buys from DB. Returns {} if table doesn't exist."""
    try:
        from psycopg2.extras import RealDictCursor
        sql = """
            SELECT ticker,
                   COUNT(*) AS buy_count,
                   SUM(shares) AS total_shares,
                   SUM(value) AS total_value,
                   MAX(transaction_date) AS last_buy
            FROM insider_transactions
            WHERE transaction_type ILIKE '%%buy%%'
              AND transaction_date >= current_date - %(days)s * interval '1 day'
            GROUP BY ticker
            HAVING COUNT(*) >= 1
            ORDER BY total_value DESC NULLS LAST
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {"days": days})
            rows = cur.fetchall()
        return {r["ticker"]: dict(r) for r in rows}
    except Exception:
        return {}


def score_insider_signal(insider: Optional[dict], vol_surge: float) -> float:
    """Score 0-1: insider DB data (preferred) or vol-surge proxy."""
    if insider:
        buy_count = insider.get("buy_count", 0) or 0
        total_value = float(insider.get("total_value") or 0)
        if buy_count >= 3 or total_value >= 1_000_000:
            return 1.0
        if buy_count >= 2 or total_value >= 500_000:
            return 0.8
        return 0.6

    # Fallback: volume surge proxy for informed buying
    if vol_surge >= 2.5:
        return 0.9
    if vol_surge >= 2.0:
        return 0.75
    if vol_surge >= 1.5:
        return 0.55
    if vol_surge >= 1.2:
        return 0.35
    return 0.1


def score_technical_setup(df: pd.DataFrame) -> dict:
    """Simplified technical score for insider-context confirmation."""
    if len(df) < 60:
        return {"tech_score": 0.0, "rsi": np.nan, "macd_hist": np.nan,
                "current_price": np.nan, "high_52w": np.nan, "pct_off_high": np.nan}

    close = df["close"]
    rsi = compute_rsi(close)
    _, _, histogram = compute_macd(close)
    ema_short = compute_ema(close, EMA_SHORT)
    ema_long = compute_ema(close, EMA_LONG)
    ema_50 = compute_ema(close, EMA_TREND)
    vol_ratio = compute_volume_ratio(df)

    rsi_val = rsi.iloc[-1]
    macd_val = histogram.iloc[-1]
    macd_prev = histogram.iloc[-2]

    # For insider buys, any RSI below 70 is acceptable (not chasing overbought)
    if rsi_val < 40:
        s_rsi = 0.8  # oversold + insider = great setup
    elif rsi_val < 55:
        s_rsi = 1.0  # ideal
    elif rsi_val < 70:
        s_rsi = 0.7
    else:
        s_rsi = 0.3  # overbought, insider buying less relevant as entry

    # EMA — above 50 EMA is a plus, not required
    price = close.iloc[-1]
    above_50 = price > ema_50.iloc[-1]
    s_ema = 0.8 if above_50 else 0.4

    # MACD improving (doesn't have to be positive for reversal-type insider buys)
    if macd_val > 0 and macd_val > macd_prev:
        s_macd = 1.0
    elif macd_val > macd_prev:
        s_macd = 0.7
    elif macd_val > 0:
        s_macd = 0.5
    else:
        s_macd = 0.2

    # Volume accumulation
    s_vol = min(1.0, vol_ratio / 1.5)

    tech_score = 0.30 * s_rsi + 0.25 * s_ema + 0.25 * s_macd + 0.20 * s_vol

    return {
        "tech_score": tech_score,
        "rsi": rsi_val,
        "macd_hist": macd_val,
        "vol_ratio": vol_ratio,
        "above_50_ema": above_50,
        "current_price": price,
        "high_52w": close.max(),
        "pct_off_high": (1 - price / close.max()) * 100 if close.max() > 0 else 0,
    }


def run_screen(days: int = 60, single_ticker: str = None,
               broad: bool = False, min_vol_surge: float = 1.2) -> List[dict]:
    conn = _conn()
    try:
        quality = load_quality_tickers(conn, broad=broad)
        insider_data = _load_insider_tickers(conn, days=days)
        if single_ticker:
            tickers = [single_ticker.upper()]
        else:
            # Include tickers from insider DB + full quality universe
            tickers = list(set(list(quality.keys()) + list(insider_data.keys())))
        load_tickers = list(set(tickers + ["SPY"]))
        prices = load_prices(conn, load_tickers)
    finally:
        conn.close()

    spy_regime = None
    if "SPY" in prices:
        spy_regime = compute_spy_regime(prices["SPY"])

    using_db = bool(insider_data)

    results = []
    for t in tickers:
        df = prices.get(t)
        if df is None or df.empty:
            continue

        vol_surge = compute_volume_surge(df)
        insider = insider_data.get(t)

        # If no DB data, filter to meaningful vol surge
        if not using_db and vol_surge < min_vol_surge:
            continue

        tech = score_technical_setup(df)
        s_insider = score_insider_signal(insider, vol_surge)

        composite = (0.55 * s_insider + 0.45 * tech["tech_score"]) * 100

        # Regime boost
        if spy_regime is True:
            composite = min(100.0, composite * 1.03)

        q = quality.get(t, {})

        row = {
            "ticker": t,
            "sleeve": "insider",
            "score": round(composite, 1),
            "insider_db": using_db,
            "vol_surge": round(vol_surge, 2),
            **q,
            **{k: v for k, v in tech.items() if k != "tech_score"},
        }
        if insider:
            row["insider_buy_count"] = insider.get("buy_count")
            row["insider_total_value"] = insider.get("total_value")
            row["insider_last_buy"] = str(insider.get("last_buy", ""))
        if spy_regime is not None:
            row["spy_regime"] = spy_regime

        results.append(row)

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def print_summary(results: List[dict], days: int):
    using_db = any(r.get("insider_db") for r in results)
    mode = "insider DB buys" if using_db else "volume burst proxy"
    print(f"\n{'=' * 100}")
    print(f"  INSIDER BURST SCREEN — Quality Names with Informed Buying ({mode})")
    print(f"  {date.today().isoformat()}  |  Lookback: {days}d  |  {len(results)} candidates")
    print(f"{'=' * 100}")
    if not results:
        print("\n  No candidates found.\n")
        return
    print(f"\n  {'TICKER':<7} {'COMPANY':<25} {'SECTOR':<20} {'SCORE':>6} "
          f"{'VOL↑':>5} {'RSI':>5} {'PRICE':>8} {'%OFF':>6}")
    print(f"  {'─'*7} {'─'*25} {'─'*20} {'─'*6} {'─'*5} {'─'*5} {'─'*8} {'─'*6}")
    for r in results:
        rsi_s = f"{r['rsi']:.0f}" if r.get("rsi") and not (isinstance(r["rsi"], float) and np.isnan(r["rsi"])) else " N/A"
        price_s = f"{r['current_price']:.2f}" if r.get("current_price") and not np.isnan(r.get("current_price", float('nan'))) else "  N/A"
        poff_s = f"{r['pct_off_high']:.1f}%" if r.get("pct_off_high") is not None else "  N/A"
        print(f"  {r['ticker']:<7} {(r.get('company_name') or '')[:24]:<25} "
              f"{(r.get('sector') or '')[:19]:<20} "
              f"{r['score']:>5.0f} {r['vol_surge']:>5.2f} {rsi_s:>5} "
              f"{price_s:>8} {poff_s:>6}")


def main():
    ap = argparse.ArgumentParser(description="Insider burst screen")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--ticker")
    ap.add_argument("--broad", action="store_true")
    ap.add_argument("--days", type=int, default=60, help="insider lookback window (days)")
    ap.add_argument("--min-surge", type=float, default=1.2,
                    help="minimum vol surge for fallback mode (default 1.2)")
    args = ap.parse_args()

    results = run_screen(
        days=args.days,
        single_ticker=args.ticker,
        broad=args.broad,
        min_vol_surge=args.min_surge,
    )
    print_summary(results[:args.top], args.days)


if __name__ == "__main__":
    main()
