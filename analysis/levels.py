"""
Watchtower — Support / Resistance level engine.

Data-derived horizontal levels, the way a discretionary trader marks them:
  1. Find swing highs / lows (pivots) on the daily AND 4-hour, ~8 months back.
  2. Cluster nearby pivots into horizontal levels (volatility-scaled band).
  3. Count touches per level; levels touched more often matter more.
  4. Star-rate 1-5 by touches + multi-timeframe confluence + recency.
  5. Split into support (below price) / resistance (above) and return the
     nearest of each, plus the full ranked list.

This is a CONTEXT layer, not a signal: it tells you whether a mover has room
to run or is walking into a wall. Pure Python over Polygon agg bars — no
pandas, two API calls per ticker, safe to compute on demand in the drawer.
"""
import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Tuning (all overridable; defaults chosen to match an 8-month discretionary read)
LOOKBACK_DAYS = 245          # ~8 trading months of daily history
PIVOT_LEFT = 3               # bars on each side that a pivot must dominate
PIVOT_RIGHT = 3
MIN_TOUCHES = 2              # a single pivot isn't a level
TOL_MIN = 0.004             # cluster band floor (0.4% of price)
TOL_MAX = 0.020             # cluster band ceiling (2.0% of price)
TF_WEIGHT = {"daily": 1.0, "4h": 0.6}   # daily pivots count more than 4h


def _pivots(bars: List[dict], tf: str, left: int, right: int) -> List[dict]:
    """Fractal pivots: a pivot high/low dominates `left` bars before and
    `right` after. Returns [{price, kind, tf, recency}] (recency 0..1, 1=newest)."""
    out: List[dict] = []
    n = len(bars)
    for i in range(left, n - right):
        hi = bars[i].get("high")
        lo = bars[i].get("low")
        win = bars[i - left:i + right + 1]
        if hi is not None and all(b.get("high") is not None for b in win):
            if hi >= max(b["high"] for b in win):
                out.append({"price": hi, "kind": "high", "tf": tf, "recency": (i + 1) / n})
        if lo is not None and all(b.get("low") is not None for b in win):
            if lo <= min(b["low"] for b in win):
                out.append({"price": lo, "kind": "low", "tf": tf, "recency": (i + 1) / n})
    return out


def _atr_pct(daily: List[dict], period: int = 14) -> float:
    """ATR as a fraction of the last close — sets the volatility-scaled band."""
    if len(daily) < period + 1:
        return 0.01
    trs = []
    for i in range(1, len(daily)):
        h, l = daily[i].get("high"), daily[i].get("low")
        pc = daily[i - 1].get("close")
        if None in (h, l, pc):
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.01
    atr = sum(trs[-period:]) / min(len(trs), period)
    last = daily[-1].get("close") or 0
    return (atr / last) if last else 0.01


def _cluster(points: List[dict], tol_frac: float) -> List[dict]:
    """Merge pivots whose prices sit within tol_frac of a running center."""
    if not points:
        return []
    pts = sorted(points, key=lambda p: p["price"])
    clusters: List[dict] = []
    cur: List[dict] = [pts[0]]

    def center(group):
        wsum = sum(TF_WEIGHT.get(p["tf"], 1.0) for p in group)
        return sum(p["price"] * TF_WEIGHT.get(p["tf"], 1.0) for p in group) / wsum

    for p in pts[1:]:
        c = center(cur)
        if abs(p["price"] - c) / c <= tol_frac:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)

    levels = []
    for g in clusters:
        touches = len(g)
        tfs = sorted({p["tf"] for p in g})
        recency = max(p["recency"] for p in g)
        confluence = len(tfs) > 1
        # Strength: raw touches + cross-timeframe bonus + recency bump
        strength = touches + (1.5 if confluence else 0.0)
        strength += 1.0 if recency > 0.8 else (0.5 if recency > 0.6 else 0.0)
        if strength >= 7:
            stars = 5
        elif strength >= 5:
            stars = 4
        elif strength >= 3.5:
            stars = 3
        elif strength >= 2:
            stars = 2
        else:
            stars = 1
        levels.append({
            "price": round(center(g), 2),
            "touches": touches,
            "stars": stars,
            "timeframes": tfs,
            "confluence": confluence,
            "recency": round(recency, 2),
        })
    return levels


def levels_from_bars(daily: List[dict], fourh: Optional[List[dict]],
                     current_price: float, max_each_side: int = 5) -> dict:
    """Pure level computation from already-fetched bars (testable, no I/O)."""
    if not daily or len(daily) < 40:
        return {"error": "insufficient daily history for levels"}
    if not current_price:
        current_price = daily[-1].get("close")
    if not current_price:
        return {"error": "no current price"}

    points = _pivots(daily, "daily", PIVOT_LEFT, PIVOT_RIGHT)
    if fourh and len(fourh) >= 40:
        points += _pivots(fourh, "4h", PIVOT_LEFT, PIVOT_RIGHT)

    tol = min(TOL_MAX, max(TOL_MIN, 0.5 * _atr_pct(daily)))
    levels = [lv for lv in _cluster(points, tol) if lv["touches"] >= MIN_TOUCHES]

    for lv in levels:
        lv["dist_pct"] = round((lv["price"] - current_price) / current_price * 100, 2)
        lv["kind"] = "resistance" if lv["price"] > current_price else "support"

    support = sorted([l for l in levels if l["kind"] == "support"],
                     key=lambda l: -l["price"])          # nearest (highest) first
    resistance = sorted([l for l in levels if l["kind"] == "resistance"],
                        key=lambda l: l["price"])         # nearest (lowest) first

    return {
        "current_price": round(current_price, 2),
        "tolerance_pct": round(tol * 100, 2),
        "nearest_support": support[0] if support else None,
        "nearest_resistance": resistance[0] if resistance else None,
        "support": support[:max_each_side],
        "resistance": resistance[:max_each_side],
    }


def compute_levels(ticker: str, current_price: Optional[float] = None,
                   max_each_side: int = 5) -> dict:
    """Fetch bars and compute support/resistance for one ticker. Returns
       {ticker, current_price, support[], resistance[], nearest_*} or {error}."""
    from analysis.polygon_data import fetch_recent_bars

    daily = fetch_recent_bars(ticker, days=LOOKBACK_DAYS, multiplier=1, timespan="day")
    if not daily or len(daily) < 40:
        return {"ticker": ticker, "error": "insufficient daily history for levels"}
    # 4-hour bars over the same span (Polygon aggregates 4×hour directly).
    fourh = fetch_recent_bars(ticker, days=LOOKBACK_DAYS, multiplier=4, timespan="hour")
    if current_price is None:
        current_price = daily[-1].get("close")

    out = levels_from_bars(daily, fourh, current_price, max_each_side)
    out["ticker"] = ticker
    return out

