"""
Watchtower — Multi-timeframe Support / Resistance level engine.

Data-derived horizontal levels, the way a discretionary trader marks them,
across SIX timeframes so you get both major structure and intraday execution:

  1W  (5y)    major structural / weekly swing — the levels that matter most
  1D  (8mo)   structural / swing levels
  4H  (4mo)   higher-timeframe swing
  1H  (2mo)   intraday swing
  15m (15d)   day-trade levels
  5m  (8d)    scalp / fine levels

For each timeframe: find swing highs/lows (pivots), then cluster ALL pivots
across timeframes into horizontal levels (volatility-scaled band). Each level
is tagged with which timeframes produced it — a price that's a pivot on the
weekly AND the daily is a high-conviction structural level. Star-rated 1-5 by
touch count, multi-timeframe confluence, and recency.

Lookback scales with timeframe: a 5-minute pivot from months ago is noise, but
a weekly pivot from years ago still anchors price — so the higher the
timeframe, the longer (and more heavily weighted) its lookback.

Pure Python over Polygon agg bars. One call per timeframe, best-effort per
timeframe (a failed/empty timeframe never breaks the others), computed on
demand in the drawer.
"""
import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Per-timeframe: Polygon (multiplier, timespan), lookback days, and a weight
# (higher timeframes carry more conviction). Ordered high→low.
TIMEFRAMES: Dict[str, dict] = {
    "1W":  {"mult": 1,  "span": "week",   "days": 1825, "weight": 1.30},
    "1D":  {"mult": 1,  "span": "day",    "days": 245, "weight": 1.00},
    "4H":  {"mult": 4,  "span": "hour",   "days": 120, "weight": 0.80},
    "1H":  {"mult": 1,  "span": "hour",   "days": 45,  "weight": 0.60},
    "15m": {"mult": 15, "span": "minute", "days": 15,  "weight": 0.45},
    "5m":  {"mult": 5,  "span": "minute", "days": 8,   "weight": 0.35},
}
DEFAULT_TFS = list(TIMEFRAMES.keys())

PIVOT_LEFT = 3
PIVOT_RIGHT = 3
MIN_TOUCHES = 2          # a single pivot isn't a level (unless multi-timeframe)
TOL_MIN = 0.0035         # cluster band floor (0.35% of price)
TOL_MAX = 0.020          # cluster band ceiling (2.0% of price)


def _pivots(bars: List[dict], tf: str, left: int, right: int) -> List[dict]:
    """Fractal pivots: a pivot high/low dominates `left` bars before and
    `right` after. Returns [{price, tf, recency}] (recency 0..1, 1=newest)."""
    out: List[dict] = []
    n = len(bars)
    for i in range(left, n - right):
        hi = bars[i].get("high")
        lo = bars[i].get("low")
        win = bars[i - left:i + right + 1]
        if hi is not None and all(b.get("high") is not None for b in win):
            if hi >= max(b["high"] for b in win):
                out.append({"price": hi, "tf": tf, "recency": (i + 1) / n})
        if lo is not None and all(b.get("low") is not None for b in win):
            if lo <= min(b["low"] for b in win):
                out.append({"price": lo, "tf": tf, "recency": (i + 1) / n})
    return out


def _atr_pct(daily: List[dict], period: int = 14) -> float:
    """ATR as a fraction of last close — sets the volatility-scaled band."""
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


def _stars(touches_by_tf: Dict[str, int], recency: float) -> int:
    """1-5 from weighted touches + multi-timeframe confluence + recency."""
    weighted = sum(touches_by_tf[tf] * TIMEFRAMES[tf]["weight"] for tf in touches_by_tf)
    distinct_tfs = len(touches_by_tf)
    strength = weighted + (distinct_tfs - 1) * 1.2          # confluence bonus per extra TF
    strength += 1.0 if recency > 0.8 else (0.5 if recency > 0.6 else 0.0)
    if strength >= 7:
        return 5
    if strength >= 5:
        return 4
    if strength >= 3.5:
        return 3
    if strength >= 2:
        return 2
    return 1


def _cluster(points: List[dict], tol_frac: float) -> List[dict]:
    """Merge pivots whose prices sit within tol_frac of a running center.
    Returns levels tagged with per-timeframe touch counts."""
    if not points:
        return []
    pts = sorted(points, key=lambda p: p["price"])

    def center(group):
        wsum = sum(TIMEFRAMES[p["tf"]]["weight"] for p in group)
        return sum(p["price"] * TIMEFRAMES[p["tf"]]["weight"] for p in group) / wsum

    clusters: List[List[dict]] = []
    cur = [pts[0]]
    for p in pts[1:]:
        if abs(p["price"] - center(cur)) / center(cur) <= tol_frac:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)

    levels = []
    for g in clusters:
        touches_by_tf: Dict[str, int] = {}
        for p in g:
            touches_by_tf[p["tf"]] = touches_by_tf.get(p["tf"], 0) + 1
        recency = max(p["recency"] for p in g)
        # order tags high→low timeframe for display
        tfs = [tf for tf in DEFAULT_TFS if tf in touches_by_tf]
        levels.append({
            "price": round(center(g), 2),
            "touches": len(g),
            "touches_by_tf": touches_by_tf,
            "timeframes": tfs,
            "stars": _stars(touches_by_tf, recency),
            "recency": round(recency, 2),
        })
    return levels


def levels_from_points(points: List[dict], daily: List[dict],
                       current_price: float, max_each_side: int = 8) -> dict:
    """Pure computation from pivot points + daily bars (for ATR/price). Testable."""
    if not current_price:
        current_price = daily[-1].get("close") if daily else None
    if not current_price:
        return {"error": "no current price"}

    tol = min(TOL_MAX, max(TOL_MIN, 0.5 * _atr_pct(daily))) if daily else 0.006
    levels = [lv for lv in _cluster(points, tol)
              if lv["touches"] >= MIN_TOUCHES or len(lv["timeframes"]) >= 2]

    for lv in levels:
        lv["dist_pct"] = round((lv["price"] - current_price) / current_price * 100, 2)
        lv["kind"] = "resistance" if lv["price"] > current_price else "support"

    support = sorted([l for l in levels if l["kind"] == "support"], key=lambda l: -l["price"])
    resistance = sorted([l for l in levels if l["kind"] == "resistance"], key=lambda l: l["price"])

    return {
        "current_price": round(current_price, 2),
        "tolerance_pct": round(tol * 100, 2),
        "timeframes": DEFAULT_TFS,
        "nearest_support": support[0] if support else None,
        "nearest_resistance": resistance[0] if resistance else None,
        "support": support[:max_each_side],
        "resistance": resistance[:max_each_side],
    }


def compute_levels(ticker: str, current_price: Optional[float] = None,
                   timeframes: Optional[List[str]] = None,
                   max_each_side: int = 8) -> dict:
    """Fetch bars across timeframes and compute tagged support/resistance.
    Returns {ticker, current_price, support[], resistance[], nearest_*} or {error}."""
    from analysis.polygon_data import fetch_recent_bars

    tfs = [t for t in (timeframes or DEFAULT_TFS) if t in TIMEFRAMES]

    # Daily is always fetched: it anchors the ATR band and the price fallback.
    daily = fetch_recent_bars(ticker, days=TIMEFRAMES["1D"]["days"], multiplier=1, timespan="day")
    if not daily or len(daily) < 40:
        return {"ticker": ticker, "error": "insufficient daily history for levels"}
    if current_price is None:
        current_price = daily[-1].get("close")

    points: List[dict] = []
    used_tfs: List[str] = []
    for tf in tfs:
        spec = TIMEFRAMES[tf]
        try:
            bars = daily if tf == "1D" else fetch_recent_bars(
                ticker, days=spec["days"], multiplier=spec["mult"], timespan=spec["span"])
            if bars and len(bars) >= (40 if tf == "1D" else 25):
                points += _pivots(bars, tf, PIVOT_LEFT, PIVOT_RIGHT)
                used_tfs.append(tf)
        except Exception as e:
            log.warning(f"[levels] {ticker} {tf} fetch failed: {e}")

    out = levels_from_points(points, daily, current_price, max_each_side)
    out["ticker"] = ticker
    out["timeframes"] = used_tfs
    return out
