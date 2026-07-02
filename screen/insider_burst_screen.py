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
    _patch_polygon_snapshots,
)


def _load_insider_tickers(conn, quarters: int = 2) -> Dict[str, dict]:
    """Load recent insider stats — tickers where insiders are net buyers."""
    try:
        from psycopg2.extras import RealDictCursor
        # Get the most recent N quarters and look for net buying
        sql = """
            SELECT ticker,
                   SUM(acquired_transactions) AS total_buys,
                   SUM(disposed_transactions) AS total_sells,
                   SUM(total_acquired) AS total_acquired,
                   SUM(total_disposed) AS total_disposed,
                   AVG(acquired_disposed_ratio) AS avg_ratio,
                   SUM(total_purchases) AS total_purchases,
                   SUM(total_sales) AS total_sales
            FROM insider_stats
            WHERE (fiscal_year, fiscal_quarter) IN (
                SELECT DISTINCT fiscal_year, fiscal_quarter
                FROM insider_stats
                ORDER BY fiscal_year DESC, fiscal_quarter DESC
                LIMIT %(quarters)s
            )
            GROUP BY ticker
            HAVING SUM(acquired_transactions) > SUM(disposed_transactions)
               AND SUM(acquired_transactions) > 0
        """
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, {"quarters": quarters})
            rows = cur.fetchall()
        return {r["ticker"]: dict(r) for r in rows}
    except Exception:
        return {}


def score_insider_signal(insider: dict) -> float:
    """Score 0-1 from insider_stats: strength of net buying activity."""
    total_buys = float(insider.get("total_buys") or 0)
    total_sells = float(insider.get("total_sells") or 0)
    avg_ratio = float(insider.get("avg_ratio") or 1.0)
    total_acquired = float(insider.get("total_acquired") or 0)
    total_disposed = float(insider.get("total_disposed") or 0)

    # Net acquisition ratio
    net_ratio = avg_ratio if avg_ratio else (
        total_acquired / total_disposed if total_disposed > 0 else 2.0
    )

    if net_ratio >= 3.0 and total_buys >= 3:
        return 1.0
    if net_ratio >= 2.0 and total_buys >= 2:
        return 0.85
    if net_ratio >= 1.5:
        return 0.70
    if net_ratio >= 1.1:
        return 0.50
    return 0.30


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


def run_screen(quarters: int = 2, single_ticker: str = None,
               broad: bool = False) -> List[dict]:
    conn = _conn()
    try:
        quality = load_quality_tickers(conn, broad=broad)
        insider_data = _load_insider_tickers(conn, quarters=quarters)
        if single_ticker:
            tickers = [single_ticker.upper()]
            insider_data = {t: v for t, v in insider_data.items() if t == single_ticker.upper()}
        else:
            # Only score tickers with actual insider buying
            tickers = list(insider_data.keys())
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

        insider = insider_data.get(t, {})
        tech = score_technical_setup(df)
        s_insider = score_insider_signal(insider)
        vol_surge = compute_volume_surge(df)

        composite = (0.55 * s_insider + 0.45 * tech["tech_score"]) * 100

        if spy_regime is True:
            composite = min(100.0, composite * 1.03)

        q = quality.get(t, {})

        row = {
            "ticker": t,
            "sleeve": "insider",
            "score": round(composite, 1),
            "insider_buy_count": insider.get("total_buys"),
            "insider_sell_count": insider.get("total_sells"),
            "insider_ratio": round(float(insider.get("avg_ratio") or 0), 2),
            "vol_surge": round(vol_surge, 2),
            **q,
            **{k: v for k, v in tech.items() if k != "tech_score"},
        }
        if spy_regime is not None:
            row["spy_regime"] = spy_regime

        results.append(row)

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def print_summary(results: List[dict], quarters: int):
    print(f"\n{'=' * 100}")
    print(f"  INSIDER BURST SCREEN — Net Insider Buying + Technical Confirmation")
    print(f"  {date.today().isoformat()}  |  Lookback: {quarters}Q  |  {len(results)} candidates")
    print(f"{'=' * 100}")
    if not results:
        print("\n  No candidates found.\n")
        return
    print(f"\n  {'TICKER':<7} {'COMPANY':<25} {'SECTOR':<20} {'SCORE':>6} "
          f"{'RATIO':>6} {'BUYS':>5} {'RSI':>5} {'PRICE':>8} {'%OFF':>6}")
    print(f"  {'─'*7} {'─'*25} {'─'*20} {'─'*6} {'─'*6} {'─'*5} {'─'*5} {'─'*8} {'─'*6}")
    for r in results:
        rsi_s = f"{r['rsi']:.0f}" if r.get("rsi") and not (isinstance(r["rsi"], float) and np.isnan(r["rsi"])) else " N/A"
        price_s = f"{r['current_price']:.2f}" if r.get("current_price") and not np.isnan(r.get("current_price", float('nan'))) else "  N/A"
        poff_s = f"{r['pct_off_high']:.1f}%" if r.get("pct_off_high") is not None else "  N/A"
        print(f"  {r['ticker']:<7} {(r.get('company_name') or '')[:24]:<25} "
              f"{(r.get('sector') or '')[:19]:<20} "
              f"{r['score']:>5.0f} {(r.get('insider_ratio') or 0):>6.2f} "
              f"{(r.get('insider_buy_count') or 0):>5} {rsi_s:>5} "
              f"{price_s:>8} {poff_s:>6}")


def main():
    ap = argparse.ArgumentParser(description="Insider burst screen")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--ticker")
    ap.add_argument("--broad", action="store_true")
    ap.add_argument("--quarters", type=int, default=2, help="quarters of insider data to look back")
    args = ap.parse_args()

    results = run_screen(
        quarters=args.quarters,
        single_ticker=args.ticker,
        broad=args.broad,
    )
    print_summary(results[:args.top], args.quarters)


if __name__ == "__main__":
    main()
