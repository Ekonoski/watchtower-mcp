"""
Watchtower — Momentum continuation screen (GMMSS Sleeve 2).

Sibling to reversal_screen.py. Finds quality stocks where momentum is
*accelerating*, not reversing. The "strong getting stronger" thesis:
catch quality names in established uptrends (or early up-and-comers in
heating sectors) before they extend further.

Key GMMSS / up-and-comer additions:
- Regime (SPY >~200MA proxy) + RS vs SPY attached (bull regime + relative strength tilt scores).
- Sector heat bias: names in sectors with strong aggregate recent returns / momentum get a small boost.
- Designed to run alongside reversal (Sleeve 1) and breakdown (Sleeve 3).
- Promoted to first-class daily/MCP citizen.

Differs from reversal:
  1. Near 52w highs (within --max-pullback), not deep drawdowns.
  2. Trend-stack / MACD-widening / RSI 50-75 zone (continuation) vs recovery signals.

Quality gate: same (S&P 500 Compounder or --broad).

Usage:
    set -a && source .env && set +a
    python3 screen/momentum_screen.py
    python3 screen/momentum_screen.py --broad
    python3 screen/momentum_screen.py --ticker MSFT
    python3 screen/momentum_screen.py --max-pullback 5   # within 5% of high
    python3 screen/momentum_screen.py --all
    python3 screen/momentum_screen.py --with-plan
"""
import argparse
import sys
from datetime import date
from typing import Dict, List, Optional

try:
    import numpy as np
    import pandas as pd
except ImportError as e:
    print(f"ERROR: missing dependency: {e}.  Run:  python3 -m pip install --user "
          f"pandas numpy psycopg2-binary", file=sys.stderr)
    sys.exit(1)

# Reuse DB connection, data loaders, and indicator math from reversal_screen.
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from reversal_screen import (
    _conn,
    load_quality_tickers,
    load_prices,
    compute_rsi, compute_ema, compute_macd,
    resample_weekly,
    compute_volume_ratio,
    compute_volume_surge,
    compute_spy_regime,
    EMA_SHORT, EMA_LONG, EMA_TREND,
    RSI_PERIOD,
    _patch_polygon_snapshots,
)

# Polygon for live high-fidelity data (GMMSS momentum/up-and-comers)
try:
    from analysis.polygon_data import (
        compute_live_technicals_from_polygon,
    )
except Exception:
    compute_live_technicals_from_polygon = lambda t, d=60: {}

# Scoring weights (sum to 1.0)
W_TREND_STACK = 0.20
W_MACD_STRENGTH = 0.20
W_RSI_ZONE = 0.15
W_PRICE_STRENGTH = 0.15
W_VOLUME = 0.10
W_WEEKLY = 0.20


def score_trend_stack(ema_short_v: float, ema_long_v: float, ema_50_v: float,
                      ema_short_prev: float, ema_long_prev: float,
                      ema_50_prev: float) -> float:
    """Score 0-1: reward proper trend stacking (8 > 13 > 50, all rising)."""
    if any(np.isnan(v) for v in [ema_short_v, ema_long_v, ema_50_v]):
        return 0.0

    stacked = ema_short_v > ema_long_v > ema_50_v
    short_rising = ema_short_v > ema_short_prev
    long_rising = ema_long_v > ema_long_prev
    trend_rising = ema_50_v > ema_50_prev

    if stacked and short_rising and long_rising and trend_rising:
        return 1.0
    if stacked and short_rising and long_rising:
        return 0.85
    if stacked:
        return 0.6
    if ema_short_v > ema_long_v and ema_long_v < ema_50_v:
        return 0.3  # short above long but long still below 50
    return 0.1


def score_macd_strength(hist_current: float, hist_prev: float,
                        hist_prev5: float) -> float:
    """Score 0-1: reward MACD histogram positive and widening."""
    if any(np.isnan(v) for v in [hist_current, hist_prev, hist_prev5]):
        return 0.0
    if hist_current <= 0:
        return 0.1 if hist_current > hist_prev else 0.0
    # positive — check trajectory
    if hist_current > hist_prev > hist_prev5:
        return 1.0  # consistently widening
    if hist_current > hist_prev:
        return 0.8  # currently widening
    if hist_current > hist_prev5:
        return 0.5  # net widened from 5 days ago
    return 0.3  # positive but narrowing


def score_rsi_bullish_zone(rsi_current: float, rsi_slope: float) -> float:
    """Score 0-1: reward RSI in trending-bullish zone (50-70).

    >75 is overbought (penalize), 50-70 is sweet spot, <40 is wrong screen
    (should be on reversal screen instead).
    """
    if np.isnan(rsi_current):
        return 0.0
    if rsi_current > 80:
        return 0.1  # extremely overbought, mean-reversion risk
    if rsi_current > 70:
        return 0.5  # overbought but can stay
    if rsi_current >= 55:
        return 1.0  # ideal trending zone
    if rsi_current >= 50:
        return 0.8
    if rsi_current >= 45:
        return 0.5
    return 0.2


def score_price_strength(close: float, ema_50: float,
                         close_20d_ago: float) -> float:
    """Score 0-1: reward price above 50 EMA + 20-day return positive."""
    if np.isnan(close) or np.isnan(ema_50) or ema_50 == 0:
        return 0.0
    pct_vs_50 = (close - ema_50) / ema_50
    ret_20d = (close - close_20d_ago) / close_20d_ago if close_20d_ago > 0 else 0
    score = 0.0
    if pct_vs_50 > 0.05:
        score += 0.6  # comfortably above
    elif pct_vs_50 > 0:
        score += 0.4
    if ret_20d > 0.10:
        score += 0.4
    elif ret_20d > 0.05:
        score += 0.3
    elif ret_20d > 0:
        score += 0.2
    return min(1.0, score)


def score_volume(vol_ratio: float) -> float:
    """Score 0-1: reward accumulation (up-vol > down-vol)."""
    if vol_ratio >= 1.5:
        return 1.0
    if vol_ratio >= 1.2:
        return 0.8
    if vol_ratio >= 1.0:
        return 0.5
    return 0.2


def score_weekly_momentum(wdf: pd.DataFrame) -> dict:
    """Weekly confirmation: weekly EMA stack, weekly MACD positive."""
    if len(wdf) < 20:
        return {"w_score": 0.5, "w_ema_stack": False, "w_macd_hist": np.nan,
                "w_rsi": np.nan}
    close = wdf["close"]
    rsi_w = compute_rsi(close)
    _, _, hist_w = compute_macd(close)
    ema_s_w = compute_ema(close, EMA_SHORT)
    ema_l_w = compute_ema(close, EMA_LONG)

    w_rsi = rsi_w.iloc[-1]
    w_hist = hist_w.iloc[-1]
    w_hist_prev = hist_w.iloc[-2] if len(hist_w) >= 2 else 0
    w_ema_stack = ema_s_w.iloc[-1] > ema_l_w.iloc[-1]

    score = 0.0
    count = 0

    count += 1
    if w_ema_stack:
        score += 1.0
    elif ema_s_w.iloc[-1] > ema_s_w.iloc[-2]:
        score += 0.4

    count += 1
    if w_hist > 0 and w_hist > w_hist_prev:
        score += 1.0
    elif w_hist > 0:
        score += 0.7
    elif w_hist > w_hist_prev:
        score += 0.3

    count += 1
    if 50 <= w_rsi <= 75:
        score += 1.0
    elif 45 <= w_rsi < 50 or 75 < w_rsi <= 80:
        score += 0.6
    else:
        score += 0.2

    return {
        "w_score": score / count,
        "w_ema_stack": w_ema_stack,
        "w_macd_hist": w_hist,
        "w_rsi": w_rsi,
    }


# ============================================================
# Main analysis
# ============================================================
def analyze_ticker(df: pd.DataFrame) -> Optional[dict]:
    if len(df) < 60:
        return None

    close = df["close"]
    rsi = compute_rsi(close)
    _, _, histogram = compute_macd(close)
    ema_short = compute_ema(close, EMA_SHORT)
    ema_long = compute_ema(close, EMA_LONG)
    ema_50 = compute_ema(close, EMA_TREND)

    rsi_current = rsi.iloc[-1]
    rsi_prev5 = rsi.iloc[-6] if len(rsi) >= 6 else rsi.iloc[0]
    rsi_slope = (rsi_current - rsi_prev5) / 5.0

    hi_52w = close.max()
    current_close = close.iloc[-1]
    pct_off_high = (1 - current_close / hi_52w) * 100 if hi_52w > 0 else 0
    close_20d_ago = close.iloc[-21] if len(close) >= 21 else close.iloc[0]

    vol_ratio = compute_volume_ratio(df)
    vol_surge = compute_volume_surge(df)

    s_trend = score_trend_stack(
        ema_short.iloc[-1], ema_long.iloc[-1], ema_50.iloc[-1],
        ema_short.iloc[-6], ema_long.iloc[-6], ema_50.iloc[-6],
    )
    s_macd = score_macd_strength(histogram.iloc[-1], histogram.iloc[-2],
                                 histogram.iloc[-6])
    s_rsi = score_rsi_bullish_zone(rsi_current, rsi_slope)
    s_price = score_price_strength(current_close, ema_50.iloc[-1], close_20d_ago)
    s_vol = score_volume(vol_ratio)
    # Boost with surge
    if vol_surge > 1.5:
        s_vol = min(1.0, s_vol * 1.25)
    elif vol_surge > 1.2:
        s_vol = min(1.0, s_vol * 1.12)

    wdf = resample_weekly(df)
    weekly = score_weekly_momentum(wdf)

    composite = (
        W_TREND_STACK * s_trend +
        W_MACD_STRENGTH * s_macd +
        W_RSI_ZONE * s_rsi +
        W_PRICE_STRENGTH * s_price +
        W_VOLUME * s_vol +
        W_WEEKLY * weekly["w_score"]
    ) * 100

    if composite >= 75:
        signal = "STRONG BUY"
    elif composite >= 60:
        signal = "BUY"
    elif composite >= 45:
        signal = "WATCH"
    else:
        signal = "WAIT"

    return {
        "current_price": current_close,
        "high_52w": hi_52w,
        "pct_off_high": pct_off_high,
        "ret_20d_pct": ((current_close - close_20d_ago) / close_20d_ago * 100
                         if close_20d_ago > 0 else 0),
        "rsi": rsi_current,
        "rsi_slope": rsi_slope,
        "macd_hist": histogram.iloc[-1],
        "macd_hist_prev": histogram.iloc[-2],
        "ema_short": ema_short.iloc[-1],
        "ema_long": ema_long.iloc[-1],
        "ema_50": ema_50.iloc[-1],
        "vol_ratio": vol_ratio,
        "vol_surge": vol_surge,
        "s_trend": s_trend,
        "s_macd": s_macd,
        "s_rsi": s_rsi,
        "s_price": s_price,
        "s_vol": s_vol,
        "w_score": weekly["w_score"],
        "w_ema_stack": weekly["w_ema_stack"],
        "w_macd_hist": weekly["w_macd_hist"],
        "w_rsi": weekly["w_rsi"],
        "momentum_score": composite,
        "signal": signal,
    }


def run_screen(max_pullback: float = 10.0, single_ticker: str = None,
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
        # Always load SPY for regime + RS (GMMSS multi-sleeve consistency)
        load_tickers = list(set(tickers + ["SPY"]))
        prices = load_prices(conn, load_tickers)
    finally:
        conn.close()

    prices = _patch_polygon_snapshots(prices)

    spy_regime = None
    if "SPY" in prices:
        spy_regime = compute_spy_regime(prices["SPY"])

    # Sector heat / up-and-comer bias (GMMSS Sleeve 2): use dedicated scorer for consistency + future expansion (news/revisions).
    try:
        from .sector_heat import compute_sector_heat, sector_heat_boost_for_ticker
        sector_heat = compute_sector_heat(prices, quality)
    except Exception:
        sector_heat = {}
    # Fallback simple ret avg if helper unavailable (keeps momentum self-contained)
    if not sector_heat:
        sector_rets: Dict[str, list] = {}
        for t, df in prices.items():
            if t == "SPY" or df is None or len(df) < 22: continue
            q = quality.get(t, {})
            sec = q.get("sector") or "Unknown"
            c = df["close"]
            ret20 = (c.iloc[-1] / c.iloc[-21] - 1.0) if c.iloc[-21] != 0 else 0.0
            sector_rets.setdefault(sec, []).append(ret20)
        sector_heat = {sec: {"avg_ret20": float(np.mean(rets)), "heat": 0.4 if np.mean(rets)>0.04 else 0.2} for sec, rets in sector_rets.items() if rets}

    results = []
    for t in tickers:
        df = prices.get(t)
        if df is None or df.empty:
            continue
        analysis = analyze_ticker(df)
        if analysis is None:
            continue

        # Live Polygon data for fresher signals (preferred for momentum/up-and-comer detection)
        try:
            poly_live = compute_live_technicals_from_polygon(t)
            if poly_live:
                if poly_live.get("live_vol_surge"):
                    analysis["vol_surge"] = poly_live["live_vol_surge"]
                    analysis["polygon_vol_surge"] = poly_live["live_vol_surge"]
                analysis["data_source_technicals"] = poly_live.get("live_data_source", "db+polygon")
        except Exception:
            pass

        fundamentals = quality.get(t, {})
        sec = fundamentals.get("sector") or "Unknown"
        # Use the sector_heat helper (or fallback) for boost
        try:
            from .sector_heat import sector_heat_boost_for_ticker
            sec_boost = sector_heat_boost_for_ticker(t, fundamentals, sector_heat)
        except Exception:
            sec_boost = 0.0
            h = sector_heat.get(sec, {})
            avg = h.get("avg_ret20", 0.0) or h.get("heat", 0.0)  # loose
            if avg > 0.08 or h.get("heat", 0) > 0.6: sec_boost = 3.5
            elif avg > 0.04 or h.get("heat", 0) > 0.4: sec_boost = 2.0
            elif avg > 0 or h.get("heat", 0) > 0.25: sec_boost = 0.8

        # RS vs SPY (same window as reversal/breakdown)
        rs_vs_spy = None
        spy_df = prices.get("SPY")
        if spy_df is not None and len(df) > 20 and len(spy_df) > 20:
            min_len = min(40, len(df), len(spy_df))
            t_ret = (df["close"].iloc[-1] / df["close"].iloc[-min_len] - 1) if df["close"].iloc[-min_len] != 0 else 0
            s_ret = (spy_df["close"].iloc[-1] / spy_df["close"].iloc[-min_len] - 1) if spy_df["close"].iloc[-min_len] != 0 else 0
            if s_ret != 0:
                rs_vs_spy = t_ret / s_ret

        # Apply RS + regime + sector heat tilts to momentum_score
        mscore = analysis.get("momentum_score", 0.0)
        if rs_vs_spy is not None and rs_vs_spy > 1.05:
            mscore = min(100.0, mscore * 1.04)
        if spy_regime is True:
            mscore = min(100.0, mscore * 1.03)
        if sec_boost > 0:
            mscore = min(100.0, mscore + sec_boost)
        analysis["momentum_score"] = mscore

        row = {"ticker": t, "sleeve": "momentum", **fundamentals, **analysis}
        if spy_regime is not None:
            row["spy_regime"] = spy_regime
        if rs_vs_spy is not None:
            row["rs_vs_spy"] = rs_vs_spy
        row["sector_heat_boost"] = round(sec_boost, 1)

        # Filter: stocks near 52w high (pullback ≤ max_pullback)
        if show_all or single_ticker or analysis["pct_off_high"] <= max_pullback:
            if with_plan:
                import os as _os, sys as _sys
                _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
                from signals.trade_plan import compute_trade_plan
                row["plan"] = compute_trade_plan(
                    close=df["close"],
                    current_price=analysis["current_price"],
                    account_equity=account_equity,
                    risk_per_trade_pct=risk_pct,
                    atr_multiplier=atr_multiplier,
                    conviction_score=analysis["momentum_score"],
                )
            results.append(row)

    results.sort(key=lambda r: r["momentum_score"], reverse=True)
    return results


def print_summary(results: List[dict], max_pullback: float, with_plan: bool = False,
                  equity: float = 100000.0):
    width = 100 + (32 if with_plan else 0)
    print(f"\n{'=' * width}")
    print(f"  MOMENTUM CONTINUATION SCREEN — Quality Trends Accelerating (GMMSS Sleeve 2 + up-and-comer sector heat)")
    plan_note = f"  |  Trade plan: ${equity:,.0f} equity" if with_plan else ""
    print(f"  {date.today().isoformat()}  |  Filter: ≤{max_pullback:.0f}% off 52w high  |  {len(results)} candidates{plan_note}")
    print(f"{'=' * width}")

    if not results:
        print("\n  No candidates found.\n")
        return

    if with_plan:
        print(f"\n  {'TICKER':<7} {'COMPANY':<22} {'PRICE':>8} "
              f"{'20D%':>6} {'SCORE':>5} {'SIGNAL':<11} "
              f"{'ATR':>5} {'STOP':>7} {'STOP%':>6} {'SHARES':>6} {'POS%':>6}")
        print(f"  {'\u2500' * 7} {'\u2500' * 22} {'\u2500' * 8} "
              f"{'\u2500' * 6} {'\u2500' * 5} {'\u2500' * 11} "
              f"{'\u2500' * 5} {'\u2500' * 7} {'\u2500' * 6} {'\u2500' * 6} {'\u2500' * 6}")
        for r in results:
            plan = r.get("plan", {})
            atr_s = f"{plan['atr']:>4.2f}" if plan.get("atr") else " N/A"
            stop_s = f"{plan['stop_price']:>7.2f}" if plan.get("stop_price") else "    N/A"
            stop_pct_s = f"{plan['stop_pct']:>4.1f}%" if plan.get("stop_pct") else " N/A "
            print(f"  {r['ticker']:<7} {(r.get('company_name') or '')[:21]:<22} "
                  f"{r['current_price']:>8.2f} "
                  f"{r['ret_20d_pct']:>+5.1f}% "
                  f"{r['momentum_score']:>5.0f} "
                  f"{r['signal']:<11} "
                  f"{atr_s} {stop_s} {stop_pct_s} "
                  f"{plan.get('shares', 0):>6d} {plan.get('position_pct', 0):>5.1f}%")
        return

    print(f"\n  {'TICKER':<7} {'COMPANY':<25} {'SECTOR':<20} {'PRICE':>8} {'%OFF':>6} "
          f"{'20D%':>6} {'SCORE':>6} {'RSI':>5} {'MACD':>6} {'SIGNAL':<11}")
    print(f"  {'\u2500' * 7} {'\u2500' * 25} {'\u2500' * 20} {'\u2500' * 8} {'\u2500' * 6} "
          f"{'\u2500' * 6} {'\u2500' * 6} {'\u2500' * 5} {'\u2500' * 6} {'\u2500' * 11}")

    for r in results:
        macd_dir = "+" if r["macd_hist"] > 0 else "\u2212"
        company = (r.get("company_name") or "")[:24]
        sector = (r.get("sector") or "")[:19]
        print(f"  {r['ticker']:<7} {company:<25} {sector:<20} "
              f"{r['current_price']:>8.2f} "
              f"{r['pct_off_high']:>5.1f}% "
              f"{r['ret_20d_pct']:>+5.1f}% "
              f"{r['momentum_score']:>5.0f} "
              f"  {r['rsi']:>3.0f} "
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
    print(f"    Pullback:    {r['pct_off_high']:.1f}%")
    print(f"    20-day ret:  {r['ret_20d_pct']:+.1f}%")

    print(f"\n  FUNDAMENTALS")
    roic = r.get("roic_5yr_avg")
    rev = r.get("revenue_growth_3yr")
    gm = r.get("gross_margin")
    nd = r.get("net_debt_to_ebitda")
    if roic: print(f"    ROIC 5yr avg:      {float(roic)*100:.1f}%")
    if rev: print(f"    Revenue growth 3yr:{float(rev)*100:.1f}%")
    print(f"    FCF positive years:{r.get('fcf_positive_years', 'N/A')}")
    if gm: print(f"    Gross margin:      {float(gm)*100:.1f}%")
    if nd: print(f"    Net Debt/EBITDA:   {float(nd):.2f}")

    print(f"\n  TECHNICAL INDICATORS (Daily)")
    print(f"    RSI (14):          {r['rsi']:.1f}  (slope {r['rsi_slope']:+.2f}/day)")
    macd_dir = "positive" if r["macd_hist"] > 0 else "negative"
    macd_trend = "widening" if r["macd_hist"] > r["macd_hist_prev"] else "narrowing"
    print(f"    MACD histogram:    {r['macd_hist']:+.3f} ({macd_dir}, {macd_trend})")
    stack_str = "8 > 13 > 50 \u2713" if r["ema_short"] > r["ema_long"] > r["ema_50"] else "not stacked"
    print(f"    EMA stack:         {stack_str}  (8={r['ema_short']:.2f}, 13={r['ema_long']:.2f}, 50={r['ema_50']:.2f})")
    print(f"    Volume up/dn:      {r['vol_ratio']:.2f}x")

    print(f"\n  DAILY SCORE BREAKDOWN")
    print(f"    Trend stacking:    {r['s_trend']:.2f} \u00d7 {W_TREND_STACK:.0%} = {r['s_trend']*W_TREND_STACK*100:.1f}")
    print(f"    MACD strength:     {r['s_macd']:.2f} \u00d7 {W_MACD_STRENGTH:.0%} = {r['s_macd']*W_MACD_STRENGTH*100:.1f}")
    print(f"    RSI zone:          {r['s_rsi']:.2f} \u00d7 {W_RSI_ZONE:.0%} = {r['s_rsi']*W_RSI_ZONE*100:.1f}")
    print(f"    Price strength:    {r['s_price']:.2f} \u00d7 {W_PRICE_STRENGTH:.0%} = {r['s_price']*W_PRICE_STRENGTH*100:.1f}")
    print(f"    Volume accum:      {r['s_vol']:.2f} \u00d7 {W_VOLUME:.0%} = {r['s_vol']*W_VOLUME*100:.1f}")
    print(f"    Weekly confirm:    {r['w_score']:.2f} \u00d7 {W_WEEKLY:.0%} = {r['w_score']*W_WEEKLY*100:.1f}")
    print(f"    \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    w_rsi_v = r.get("w_rsi", 0)
    w_rsi_s = f"{w_rsi_v:.1f}" if w_rsi_v and not np.isnan(w_rsi_v) else "N/A"
    w_macd_v = r.get("w_macd_hist", 0)
    w_macd_s = f"{w_macd_v:+.2f}" if w_macd_v and not np.isnan(w_macd_v) else "N/A"
    print(f"\n  WEEKLY: RSI={w_rsi_s}  MACD={w_macd_s}  EMA stack: {'8>13 \u2713' if r.get('w_ema_stack') else '8<13'}")
    print(f"  MOMENTUM SCORE:    {r['momentum_score']:.0f} / 100  \u2192  {r['signal']}")

    plan = r.get("plan")
    if plan and plan.get("atr"):
        print(f"\n  TRADE PLAN  (ATR-based stop + risk-scaled size)")
        print(f"    14-day ATR proxy:  ${plan['atr']:.2f}")
        print(f"    Stop price:        ${plan['stop_price']:.2f}  ({plan['stop_pct']:.1f}% below entry)")
        print(f"    Shares to buy:     {plan['shares']:>6,d}")
        print(f"    Position size:     ${plan['position_dollars']:>10,.0f}  ({plan['position_pct']:.1f}% of equity)")
        print(f"    Dollar at risk:    ${plan['dollar_risk_actual']:>10,.0f}")
        print(f"    Conviction mult:   {plan['conviction_mult']:.2f}\u00d7 (based on score {r['momentum_score']:.0f})")


def main():
    ap = argparse.ArgumentParser(description="Momentum continuation screen — strong getting stronger")
    ap.add_argument("--max-pullback", type=float, default=10.0,
                    help="max %% off 52w high (default 10%%)")
    ap.add_argument("--ticker", help="show detailed analysis for one ticker")
    ap.add_argument("--all", action="store_true", help="show all quality names regardless of pullback")
    ap.add_argument("--broad", action="store_true",
                    help="use full universe (not just S&P 500 Compounder)")
    ap.add_argument("--top", type=int, default=25, help="top N results (default 25)")
    ap.add_argument("--with-plan", action="store_true",
                    help="append ATR stop + suggested position size to each candidate")
    ap.add_argument("--equity", type=float, default=100000,
                    help="account equity for sizing (default $100k)")
    ap.add_argument("--risk-pct", type=float, default=1.0,
                    help="%% of equity at risk per trade (default 1.0)")
    ap.add_argument("--atr-mult", type=float, default=2.0,
                    help="stop = entry - ATR_mult \u00d7 ATR (default 2.0)")
    args = ap.parse_args()

    results = run_screen(
        max_pullback=args.max_pullback,
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
        print_summary(results[:args.top], args.max_pullback,
                      with_plan=args.with_plan, equity=args.equity)


if __name__ == "__main__":
    main()
