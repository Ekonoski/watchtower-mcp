"""
Watchtower — Master composite screen (GMMSS broad fundamental + technical).

Ranks the full quality universe by a composite of fundamental strength
and current technical posture. Useful for:
- Getting a snapshot of the best-positioned names across all sleeves.
- Cross-checking reversal / momentum picks against the fundamental tier list.
- Finding overlooked quality names with improving technicals.

Score = 50% fundamentals (ROIC, revenue growth, FCF, margins) + 50% technicals
(RSI zone, EMA trend, MACD, volume).

Reuses reversal_screen helpers for DB, indicators, and regime.

Usage:
    python screen/master_screen.py
    python screen/master_screen.py --top 20
    python screen/master_screen.py --ticker AAPL
"""
import argparse
import sys
from datetime import date
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
    compute_spy_regime,
    EMA_SHORT, EMA_LONG, EMA_TREND, RSI_PERIOD,
)

# Weights
W_FUND = 0.50
W_TECH = 0.50

# Fundamental sub-weights (within 50%)
W_ROIC = 0.35
W_REV_GROWTH = 0.25
W_FCF = 0.20
W_MARGIN = 0.20

# Technical sub-weights (within 50%)
W_RSI = 0.25
W_EMA = 0.30
W_MACD = 0.25
W_VOL = 0.20


def score_fundamentals(q: dict) -> float:
    """Score 0-1 based on quality_universe fundamentals."""
    score = 0.0
    count = 0

    # ROIC
    roic = q.get("roic_5yr_avg")
    if roic is not None:
        try:
            r = float(roic)
            if r >= 0.20:
                score += W_ROIC * 1.0
            elif r >= 0.15:
                score += W_ROIC * 0.8
            elif r >= 0.10:
                score += W_ROIC * 0.5
            else:
                score += W_ROIC * 0.2
        except (TypeError, ValueError):
            pass
        count += 1

    # Revenue growth
    rev = q.get("revenue_growth_3yr")
    if rev is not None:
        try:
            r = float(rev)
            if r >= 0.15:
                score += W_REV_GROWTH * 1.0
            elif r >= 0.08:
                score += W_REV_GROWTH * 0.7
            elif r >= 0.03:
                score += W_REV_GROWTH * 0.4
            else:
                score += W_REV_GROWTH * 0.1
        except (TypeError, ValueError):
            pass
        count += 1

    # FCF positive years
    fcf = q.get("fcf_positive_years")
    if fcf is not None:
        try:
            f = int(fcf)
            if f >= 8:
                score += W_FCF * 1.0
            elif f >= 5:
                score += W_FCF * 0.7
            elif f >= 3:
                score += W_FCF * 0.4
            else:
                score += W_FCF * 0.1
        except (TypeError, ValueError):
            pass
        count += 1

    # Gross margin
    gm = q.get("gross_margin")
    if gm is not None:
        try:
            g = float(gm)
            if g >= 0.50:
                score += W_MARGIN * 1.0
            elif g >= 0.35:
                score += W_MARGIN * 0.7
            elif g >= 0.20:
                score += W_MARGIN * 0.4
            else:
                score += W_MARGIN * 0.2
        except (TypeError, ValueError):
            pass
        count += 1

    return score if count > 0 else 0.5


def score_technicals(df: pd.DataFrame) -> dict:
    """Score 0-1 based on current technical posture."""
    if len(df) < 60:
        return {"tech_score": 0.0, "rsi": np.nan, "ema_aligned": False,
                "macd_positive": False, "vol_ratio": 1.0}

    close = df["close"]
    rsi = compute_rsi(close)
    _, _, histogram = compute_macd(close)
    ema_short = compute_ema(close, EMA_SHORT)
    ema_long = compute_ema(close, EMA_LONG)
    ema_50 = compute_ema(close, EMA_TREND)

    rsi_val = rsi.iloc[-1]
    macd_hist = histogram.iloc[-1]
    macd_prev = histogram.iloc[-2]
    vol_ratio = compute_volume_ratio(df)

    # RSI: best zone is 45-70 (healthy trend, not overbought)
    if 55 <= rsi_val <= 70:
        s_rsi = 1.0
    elif 45 <= rsi_val < 55 or 70 < rsi_val <= 78:
        s_rsi = 0.75
    elif 35 <= rsi_val < 45:
        s_rsi = 0.5
    elif rsi_val > 78:
        s_rsi = 0.3
    else:
        s_rsi = 0.2

    # EMA alignment
    ema_stacked = ema_short.iloc[-1] > ema_long.iloc[-1] > ema_50.iloc[-1]
    ema_partial = ema_short.iloc[-1] > ema_long.iloc[-1]
    if ema_stacked:
        s_ema = 1.0
    elif ema_partial:
        s_ema = 0.6
    else:
        s_ema = 0.2

    # MACD
    if macd_hist > 0 and macd_hist > macd_prev:
        s_macd = 1.0
    elif macd_hist > 0:
        s_macd = 0.7
    elif macd_hist > macd_prev:
        s_macd = 0.4
    else:
        s_macd = 0.1

    # Volume
    if vol_ratio >= 1.5:
        s_vol = 1.0
    elif vol_ratio >= 1.1:
        s_vol = 0.7
    elif vol_ratio >= 0.9:
        s_vol = 0.4
    else:
        s_vol = 0.2

    tech_score = (W_RSI * s_rsi + W_EMA * s_ema + W_MACD * s_macd + W_VOL * s_vol)

    return {
        "tech_score": tech_score,
        "rsi": rsi_val,
        "ema_aligned": ema_stacked,
        "macd_positive": macd_hist > 0,
        "macd_hist": macd_hist,
        "vol_ratio": vol_ratio,
        "current_price": close.iloc[-1],
        "high_52w": close.max(),
        "pct_off_high": (1 - close.iloc[-1] / close.max()) * 100 if close.max() > 0 else 0,
    }


def run_screen(single_ticker: str = None, broad: bool = False,
               min_score: float = 0.0) -> List[dict]:
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

    spy_regime = None
    if "SPY" in prices:
        spy_regime = compute_spy_regime(prices["SPY"])

    results = []
    for t in tickers:
        df = prices.get(t)
        q = quality.get(t, {})

        fund_score = score_fundamentals(q)
        tech = score_technicals(df) if df is not None else {"tech_score": 0.0}

        composite = (W_FUND * fund_score + W_TECH * tech["tech_score"]) * 100

        # Regime boost
        if spy_regime is True:
            composite = min(100.0, composite * 1.02)

        if composite < min_score:
            continue

        row = {
            "ticker": t,
            "sleeve": "master",
            "score": round(composite, 1),
            "fund_score": round(fund_score * 100, 1),
            "tech_score": round(tech.get("tech_score", 0) * 100, 1),
            **q,
            **{k: v for k, v in tech.items() if k != "tech_score"},
        }
        if spy_regime is not None:
            row["spy_regime"] = spy_regime
        results.append(row)

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def print_summary(results: List[dict]):
    print(f"\n{'=' * 100}")
    print(f"  MASTER COMPOSITE SCREEN — Quality Universe Ranked by Fund + Tech (GMMSS)")
    print(f"  {date.today().isoformat()}  |  {len(results)} candidates")
    print(f"{'=' * 100}")
    if not results:
        print("\n  No candidates found.\n")
        return
    print(f"\n  {'TICKER':<7} {'COMPANY':<25} {'SECTOR':<20} {'SCORE':>6} "
          f"{'FUND':>5} {'TECH':>5} {'RSI':>5} {'PRICE':>8} {'%OFF':>6}")
    print(f"  {'─'*7} {'─'*25} {'─'*20} {'─'*6} {'─'*5} {'─'*5} {'─'*5} {'─'*8} {'─'*6}")
    for r in results:
        rsi_s = f"{r['rsi']:.0f}" if r.get("rsi") and not (isinstance(r["rsi"], float) and np.isnan(r["rsi"])) else " N/A"
        price_s = f"{r['current_price']:.2f}" if r.get("current_price") else "  N/A"
        poff_s = f"{r['pct_off_high']:.1f}%" if r.get("pct_off_high") is not None else "  N/A"
        print(f"  {r['ticker']:<7} {(r.get('company_name') or '')[:24]:<25} "
              f"{(r.get('sector') or '')[:19]:<20} "
              f"{r['score']:>5.0f} {r['fund_score']:>5.0f} {r['tech_score']:>5.0f} "
              f"{rsi_s:>5} {price_s:>8} {poff_s:>6}")


def main():
    ap = argparse.ArgumentParser(description="Master composite screen")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--ticker")
    ap.add_argument("--broad", action="store_true")
    ap.add_argument("--min-score", type=float, default=0.0)
    args = ap.parse_args()

    results = run_screen(
        single_ticker=args.ticker,
        broad=args.broad,
        min_score=args.min_score,
    )
    print_summary(results[:args.top])


if __name__ == "__main__":
    main()
