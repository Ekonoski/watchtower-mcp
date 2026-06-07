"""
Watchtower — Volume Burst screen (GMMSS breakout + exhaustion detector).

Detects two setups using unusual volume surges:

BREAKOUT — price clears a key level (52w high, 20/50-day resistance) on 2x+
normal volume. Institutional accumulation signal. Look for continuation.

EXHAUSTION — climactic volume spike at extended levels (overbought RSI near 52w
high) or at washed-out lows (oversold RSI near 52w low). Signals a potential
reversal — either a top forming or a capitulation bottom.

Each result is tagged with a signal_type: BREAKOUT | BREAKOUT_WATCH |
EXHAUSTION_TOP | EXHAUSTION_BOTTOM | NEUTRAL.

Reuses reversal_screen helpers for DB, indicators, and regime.

Usage:
    python screen/volume_burst_screen.py
    python screen/volume_burst_screen.py --mode breakout
    python screen/volume_burst_screen.py --mode exhaustion
    python screen/volume_burst_screen.py --min-surge 2.0
    python screen/volume_burst_screen.py --ticker AAPL
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
    compute_rsi,
    compute_ema,
    compute_macd,
    compute_volume_ratio,
    compute_volume_surge,
    compute_spy_regime,
    EMA_SHORT, EMA_LONG, EMA_TREND, RSI_PERIOD,
)

# Minimum surge to even consider a ticker
DEFAULT_MIN_SURGE = 1.75

# Key level proximity thresholds
PCT_FROM_HIGH_BREAKOUT = 2.0    # within 2% of 52w high = near breakout territory
PCT_FROM_HIGH_EXTENDED = 5.0    # within 5% of 52w high = extended
PCT_FROM_LOW_WASHED = 10.0      # within 10% of 52w low = potential capitulation bottom

# RSI thresholds
RSI_OVERBOUGHT = 72
RSI_OVERSOLD = 32


def compute_key_levels(df: pd.DataFrame) -> dict:
    """Compute support/resistance context from price history."""
    close = df["close"]
    n = len(close)

    high_52w = close.max()
    low_52w = close.min()
    current = close.iloc[-1]

    pct_from_high = (1 - current / high_52w) * 100 if high_52w > 0 else 0
    pct_from_low = (current / low_52w - 1) * 100 if low_52w > 0 else 0

    # 20-day and 50-day resistance (recent highs as proxy)
    high_20d = close.iloc[-20:].max() if n >= 20 else high_52w
    high_50d = close.iloc[-50:].max() if n >= 50 else high_52w

    # Prior day close for intraday-style breakout check
    prev_close = close.iloc[-2] if n >= 2 else current

    # Is today's close above the 20d high (ex today)?
    broke_20d = current > close.iloc[-21:-1].max() if n >= 21 else False
    broke_52w = pct_from_high <= 0.5  # within 0.5% of all-time 52w high

    return {
        "high_52w": high_52w,
        "low_52w": low_52w,
        "current_price": current,
        "pct_from_high": pct_from_high,
        "pct_from_low": pct_from_low,
        "high_20d": high_20d,
        "high_50d": high_50d,
        "prev_close": prev_close,
        "broke_20d_high": broke_20d,
        "broke_52w_high": broke_52w,
    }


def classify_signal(
    rsi: float,
    vol_surge: float,
    pct_from_high: float,
    pct_from_low: float,
    broke_20d: bool,
    broke_52w: bool,
    macd_hist: float,
    ema_short: float,
    ema_long: float,
) -> tuple:
    """
    Returns (signal_type, score, rationale).
    signal_type: BREAKOUT | BREAKOUT_WATCH | EXHAUSTION_TOP | EXHAUSTION_BOTTOM | NEUTRAL
    score: 0-100
    """
    # ── BREAKOUT ─────────────────────────────────────────────────────────────
    if broke_52w and vol_surge >= 2.0 and rsi < RSI_OVERBOUGHT:
        score = min(100, 60 + (vol_surge - 2.0) * 10 + (RSI_OVERBOUGHT - rsi) * 0.3)
        return "BREAKOUT", round(score, 1), "52w high breakout on high volume"

    if broke_20d and vol_surge >= 1.75 and macd_hist > 0 and ema_short > ema_long:
        score = min(100, 50 + (vol_surge - 1.75) * 12 + (5 - min(5, pct_from_high)) * 2)
        return "BREAKOUT", round(score, 1), "20d high breakout, trend aligned, volume surge"

    if pct_from_high <= PCT_FROM_HIGH_BREAKOUT and vol_surge >= 1.75 and macd_hist > 0:
        score = min(100, 45 + (vol_surge - 1.75) * 10)
        return "BREAKOUT_WATCH", round(score, 1), "Near 52w high with volume — watch for follow-through"

    # ── EXHAUSTION TOP ────────────────────────────────────────────────────────
    if (pct_from_high <= PCT_FROM_HIGH_EXTENDED
            and rsi >= RSI_OVERBOUGHT
            and vol_surge >= 2.0):
        score = min(100, 55 + (rsi - RSI_OVERBOUGHT) * 0.8 + (vol_surge - 2.0) * 8)
        return "EXHAUSTION_TOP", round(score, 1), "Climactic volume at overbought extended levels"

    # ── EXHAUSTION BOTTOM (capitulation) ─────────────────────────────────────
    if (pct_from_low <= PCT_FROM_LOW_WASHED
            and rsi <= RSI_OVERSOLD
            and vol_surge >= 1.75):
        score = min(100, 50 + (RSI_OVERSOLD - rsi) * 0.8 + (vol_surge - 1.75) * 10)
        return "EXHAUSTION_BOTTOM", round(score, 1), "Capitulation volume at oversold washed-out low"

    return "NEUTRAL", 0.0, ""


def analyze_ticker(df: pd.DataFrame, min_surge: float) -> Optional[dict]:
    """Full volume burst analysis for one ticker."""
    if len(df) < 60:
        return None

    vol_surge = compute_volume_surge(df)
    if vol_surge < min_surge:
        return None

    close = df["close"]
    rsi_series = compute_rsi(close)
    _, _, histogram = compute_macd(close)
    ema_short = compute_ema(close, EMA_SHORT)
    ema_long = compute_ema(close, EMA_LONG)
    ema_50 = compute_ema(close, EMA_TREND)
    vol_ratio = compute_volume_ratio(df)

    rsi_val = rsi_series.iloc[-1]
    macd_hist = histogram.iloc[-1]

    levels = compute_key_levels(df)

    signal_type, score, rationale = classify_signal(
        rsi=rsi_val,
        vol_surge=vol_surge,
        pct_from_high=levels["pct_from_high"],
        pct_from_low=levels["pct_from_low"],
        broke_20d=levels["broke_20d_high"],
        broke_52w=levels["broke_52w_high"],
        macd_hist=macd_hist,
        ema_short=ema_short.iloc[-1],
        ema_long=ema_long.iloc[-1],
    )

    if signal_type == "NEUTRAL":
        return None

    return {
        "signal_type": signal_type,
        "score": score,
        "rationale": rationale,
        "vol_surge": round(vol_surge, 2),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(rsi_val, 1),
        "macd_hist": round(macd_hist, 4),
        "ema_short": round(ema_short.iloc[-1], 2),
        "ema_long": round(ema_long.iloc[-1], 2),
        "ema_50": round(ema_50.iloc[-1], 2),
        **levels,
    }


def run_screen(
    min_surge: float = DEFAULT_MIN_SURGE,
    mode: str = "all",           # "breakout" | "exhaustion" | "all"
    single_ticker: str = None,
    broad: bool = False,
) -> List[dict]:
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
        if df is None or df.empty:
            continue

        analysis = analyze_ticker(df, min_surge=min_surge)
        if analysis is None:
            continue

        # Mode filter
        sig = analysis["signal_type"]
        if mode == "breakout" and "BREAKOUT" not in sig:
            continue
        if mode == "exhaustion" and "EXHAUSTION" not in sig:
            continue

        # Regime context: in bear regime, suppress breakout signals slightly
        score = analysis["score"]
        if spy_regime is False and "BREAKOUT" in sig:
            score = score * 0.90
        if spy_regime is True and "EXHAUSTION_BOTTOM" in sig:
            score = min(100.0, score * 1.05)

        q = quality.get(t, {})
        row = {
            "ticker": t,
            "sleeve": "volume_burst",
            **q,
            **analysis,
            "score": round(score, 1),
        }
        if spy_regime is not None:
            row["spy_regime"] = spy_regime
        results.append(row)

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def print_summary(results: List[dict], mode: str, min_surge: float):
    mode_label = {"breakout": "Breakouts", "exhaustion": "Exhaustion Signals", "all": "All Signals"}
    print(f"\n{'=' * 110}")
    print(f"  VOLUME BURST SCREEN — {mode_label.get(mode, 'All Signals')} (min surge: {min_surge:.1f}x)")
    print(f"  {date.today().isoformat()}  |  {len(results)} candidates")
    print(f"{'=' * 110}")

    if not results:
        print("\n  No candidates found.\n")
        return

    print(f"\n  {'TICKER':<7} {'COMPANY':<22} {'SIGNAL':<18} {'SCORE':>5} "
          f"{'SURGE':>5} {'RSI':>4} {'%OFF_HI':>7} {'PRICE':>8}  RATIONALE")
    print(f"  {'─'*7} {'─'*22} {'─'*18} {'─'*5} {'─'*5} {'─'*4} {'─'*7} {'─'*8}  {'─'*35}")

    for r in results:
        price_s = f"{r['current_price']:.2f}" if r.get("current_price") else "  N/A"
        print(f"  {r['ticker']:<7} {(r.get('company_name') or '')[:21]:<22} "
              f"{r['signal_type']:<18} {r['score']:>5.0f} "
              f"{r['vol_surge']:>5.2f} {r['rsi']:>4.0f} "
              f"{r['pct_from_high']:>6.1f}% {price_s:>8}  {r['rationale']}")


def main():
    ap = argparse.ArgumentParser(description="Volume burst screen — breakouts and exhaustion")
    ap.add_argument("--mode", choices=["all", "breakout", "exhaustion"], default="all")
    ap.add_argument("--min-surge", type=float, default=DEFAULT_MIN_SURGE,
                    help=f"minimum volume surge multiplier (default {DEFAULT_MIN_SURGE})")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--ticker")
    ap.add_argument("--broad", action="store_true")
    args = ap.parse_args()

    results = run_screen(
        min_surge=args.min_surge,
        mode=args.mode,
        single_ticker=args.ticker,
        broad=args.broad,
    )
    print_summary(results[:args.top], args.mode, args.min_surge)


if __name__ == "__main__":
    main()
