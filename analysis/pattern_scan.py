"""
Watchtower — classical chart-pattern scanner (weekly / daily / 4h).

One shared pivot engine (fractal swing highs/lows, same primitive as
analysis/levels.py) feeding a family of detectors for the common setups:

  bullish reversals    inverse_hs, double_bottom, falling_wedge
  bearish reversals    hs_top, double_top, rising_wedge
  continuations        bull_flag, bear_flag, asc_triangle, desc_triangle

The flagship is the inverse head & shoulders — decline → low → equal-or-
LOWER low (head) → HIGHER low (right shoulder) → neckline break. The higher
low off the head is what proves the trend change; the left shoulder and head
are allowed to be the same or similar price (double-bottom variant).

Every detector returns the same shape:
  trigger_price — the line that confirms the pattern (neckline / flag high /
                  triangle resistance / wedge bound)
  target        — measured-move objective
  invalid_level — where the structure is broken (head / flag low / last low)
  status        — 'forming' (price hasn't crossed the trigger) or 'breakout'
                  (crossed within the last few bars, not yet extended).
                  Stale or extended breaks are dropped, not listed — the
                  table only ever holds LIVE, actionable patterns.

Data:
  weekly + daily — from daily_prices in the DB (screener universe +
                   watchlist, zero API calls). The table is close-only, so
                   these timeframes detect closing-price structure.
  4h             — Polygon 4-hour bars (true OHLC) for a bounded candidate set:
                   watchlist ∪ weekly/daily hits ∪ existing 4h rows ∪ the
                   ~350 most liquid names by dollar volume

Results land in pattern_scan (migration 0061). Each nightly run replaces a
timeframe wholesale; detected_at survives while the structure's anchor bar
is unchanged, so "days on list" is meaningful. Intraday, forming patterns'
triggers are checked against live prices each scan and emit
PATTERN_BREAKOUT / PATTERN_BREAKDOWN signal rows through the normal alert
pipeline.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)

# Per-timeframe knobs. `scale` multiplies every percent threshold — a weekly
# pattern needs real depth to mean anything, a 4h pattern is tighter.
# Bar-count knobs (widths, windows) are per-timeframe absolutes.
TF = {
    "weekly": dict(pivot_k=2, min_bars=45, recent=16, break_recent=5,
                   max_ext=0.12, scale=1.7, max_width=110, min_sep=3,
                   flag_max=8, run_len=13, tri_window=34,
                   cup_min=12, cup_max=80, handle_max=8,
                   range_lens=(104, 78, 52, 26)),
    "daily":  dict(pivot_k=3, min_bars=80, recent=30, break_recent=7,
                   max_ext=0.08, scale=1.0, max_width=160, min_sep=4,
                   flag_max=15, run_len=21, tri_window=50,
                   cup_min=25, cup_max=150, handle_max=15,
                   range_lens=(180, 120, 90, 60)),
    "4h":     dict(pivot_k=3, min_bars=55, recent=36, break_recent=12,
                   max_ext=0.05, scale=0.6, max_width=170, min_sep=4,
                   flag_max=18, run_len=26, tri_window=60,
                   cup_min=30, cup_max=150, handle_max=18,
                   range_lens=(320, 240, 160, 100)),
}

PATTERN_NAMES = {
    "inverse_hs": "Inverse H&S", "hs_top": "H&S Top",
    "double_bottom": "Double Bottom", "double_top": "Double Top",
    "bull_flag": "Bull Flag", "bear_flag": "Bear Flag",
    "asc_triangle": "Asc Triangle", "desc_triangle": "Desc Triangle",
    "falling_wedge": "Falling Wedge", "rising_wedge": "Rising Wedge",
    "cup_handle": "Cup & Handle",
    "range_breakout": "Range Breakout", "range_breakdown": "Range Breakdown",
}

FOUR_H_LIQUID_TOP = 350     # most-liquid names always scanned on 4h
FOUR_H_MAX_CANDIDATES = 650
FOUR_H_WORKERS = 6


# ── Pivot engine ─────────────────────────────────────────────────────────────

def _pivots(vals: list, k: int, kind: str) -> list:
    """Fractal pivots on a value series: index i is a pivot if it's the
    extreme of i±k. Pivots closer than k bars apart are the same swing (flat
    plateaus print several) and are merged, keeping the more extreme — and on
    ties the more recent. Returns [(idx, value)] ascending."""
    raw = []
    n = len(vals)
    for i in range(k, n - k):
        v = vals[i]
        if v is None:
            continue
        win = [x for x in vals[i - k:i + k + 1] if x is not None]
        if not win:
            continue
        if kind == "low" and v <= min(win):
            raw.append((i, float(v)))
        elif kind == "high" and v >= max(win):
            raw.append((i, float(v)))
    out: list = []
    for i, v in raw:
        if out and i - out[-1][0] <= k:
            pi, pv = out[-1]
            better = (v <= pv) if kind == "low" else (v >= pv)
            if better:
                out[-1] = (i, v)
            continue
        out.append((i, v))
    return out


def _ctx(bars: list, timeframe: str):
    """Precompute everything the detectors share, or None if unusable."""
    cfg = TF[timeframe]
    n = len(bars)
    if n < cfg["min_bars"]:
        return None
    closes = [b.get("close") for b in bars]
    if closes[-1] is None or closes[-1] <= 0:
        return None
    highs = [b.get("high") for b in bars]
    lows = [b.get("low") for b in bars]
    vols = [float(b.get("volume") or 0) for b in bars]
    k = cfg["pivot_k"]
    return {
        "bars": bars, "n": n, "tf": timeframe, "cfg": cfg,
        "closes": [float(c) if c is not None else None for c in closes],
        "highs": [float(h) if h is not None else None for h in highs],
        "lows": [float(x) if x is not None else None for x in lows],
        "vols": vols,
        "plows": _pivots(lows, k, "low"),
        "phighs": _pivots(highs, k, "high"),
        "last": float(closes[-1]),
    }


def _status(ctx, start_idx: int, trigger: float, direction: str):
    """'forming' / 'breakout' / None (stale or extended break — not listable).
    A break that has already retreated back through the trigger counts as
    forming again (retest)."""
    closes, n, cfg = ctx["closes"], ctx["n"], ctx["cfg"]
    last = ctx["last"]
    if direction == "bullish":
        cross = next((i for i in range(start_idx + 1, n)
                      if closes[i] is not None and closes[i] > trigger), None)
        if cross is None or last < trigger:
            return "forming"
        if (n - 1) - cross > cfg["break_recent"]:
            return None
        if last > trigger * (1 + cfg["max_ext"]):
            return None
        return "breakout"
    cross = next((i for i in range(start_idx + 1, n)
                  if closes[i] is not None and closes[i] < trigger), None)
    if cross is None or last > trigger:
        return "forming"
    if (n - 1) - cross > cfg["break_recent"]:
        return None
    if last < trigger * (1 - cfg["max_ext"]):
        return None
    return "breakout"


def _mk(ctx, pattern, direction, status, trigger, target, invalid,
        anchor_idx, body_start, points, quality) -> dict:
    """Assemble the standard result row + score. quality is 0-25."""
    last = ctx["last"]
    dist = (last - trigger) / trigger * 100.0
    score = 40.0 + max(0.0, min(25.0, quality))
    # Proximity to the trigger (forming) / freshness (breakout)
    if status == "breakout":
        score += 10.0
    else:
        away = abs(dist)
        score += 8.0 if away <= 3.0 else (4.0 if away <= 6.0 else 0.0)
    # Recent volume vs pattern-body volume
    try:
        body = ctx["vols"][max(0, body_start):ctx["n"] - 5]
        recent = ctx["vols"][-5:]
        if body and recent and sum(body) > 0:
            vr = (sum(recent) / len(recent)) / (sum(body) / len(body))
            score += 8.0 if vr >= 1.3 else (4.0 if vr >= 1.0 else 0.0)
    except Exception:
        pass
    d = ctx["bars"][anchor_idx].get("date")
    return {
        "pattern": pattern, "direction": direction, "status": status,
        "trigger_price": round(trigger, 4), "target": round(max(target, 0.01), 4),
        "invalid_level": round(invalid, 4) if invalid is not None else None,
        "anchor_price": round(float(points.get("_anchor_price", trigger)), 4),
        "anchor_date": d,
        "points": {k: v for k, v in points.items() if not k.startswith("_")},
        "last_close": round(last, 4),
        "dist_to_trigger_pct": round(dist, 2),
        "score": round(min(score, 100.0), 1),
    }


def _pt(ctx, idx, price):
    d = ctx["bars"][idx].get("date")
    return {"date": str(d) if d is not None else None, "price": round(price, 4)}


def _robust_extreme(vals: list, side: str):
    """Spike-resistant trigger line: the level a chartist would draw.

    A neckline defined by the single most extreme print (a one-day spike
    that immediately failed) sits above where the market actually fought;
    the level the OTHER bars respected is the real line. For windows long
    enough to afford it, the most extreme 1-2 bars are treated as overshoot
    and the trigger sits at the next value: drop 1 outlier at 15+ bars,
    2 at 60+. Short windows keep the true extreme untouched."""
    vs = sorted(v for v in vals if v is not None)
    if not vs:
        return None
    k = 2 if len(vs) >= 60 else (1 if len(vs) >= 15 else 0)
    return vs[-1 - k] if side == "high" else vs[k]


# ── Reversals: head & shoulders (both ways) ──────────────────────────────────

def _det_inverse_hs(ctx):
    """Decline → low → equal-or-lower low (head) → HIGHER low → neckline.
    The one that matters most: the higher low off the head IS the signal."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    min_hl, min_depth = 0.020 * s, 0.06 * s
    plows = ctx["plows"]
    if len(plows) < 3:
        return None
    l3_idx, l3 = plows[-1]
    if (n - 1) - l3_idx > cfg["recent"]:
        return None
    win_start = max(0, l3_idx - cfg["max_width"])
    head_cands = [(i, p) for i, p in plows if win_start <= i <= l3_idx - cfg["min_sep"]]
    if not head_cands:
        return None
    l2_idx, l2 = min(head_cands, key=lambda t: t[1])
    if l3 < l2 * (1 + min_hl):
        return None  # no higher low — no pattern
    lows_after = [x for x in ctx["lows"][l2_idx + 1:] if x is not None]
    if not lows_after or min(lows_after) < l2 * 0.999:
        return None  # something after the head undercut it
    ls_cands = [(i, p) for i, p in plows if win_start <= i <= l2_idx - cfg["min_sep"]]
    if not ls_cands:
        return None
    l1_idx, l1 = min(ls_cands, key=lambda t: t[1])
    neck = _robust_extreme(ctx["highs"][l2_idx:l3_idx + 1], "high")
    if not neck or neck <= 0 or l3 >= neck * 0.995 or l1 >= neck:
        return None
    depth = (neck - l2) / l2
    if depth < min_depth:
        return None
    pre = [x for x in ctx["highs"][max(0, l1_idx - (l3_idx - l1_idx)):l1_idx] if x is not None]
    if not pre or max(pre) < neck * (1 + 0.03 * s):
        return None  # didn't come DOWN into this — basing noise, not a reversal
    if ctx["last"] <= l3:
        return None  # higher low already violated
    status = _status(ctx, l3_idx, neck, "bullish")
    if status is None:
        return None
    hl = (l3 - l2) / l2
    quality = min(10.0, 5.0 * depth / min_depth) + min(10.0, 6.0 * hl / min_hl)
    lw, rw = l2_idx - l1_idx, l3_idx - l2_idx
    if lw > 0 and 0.4 <= rw / lw <= 2.5:
        quality += 5.0
    points = {"left": _pt(ctx, l1_idx, l1), "head": _pt(ctx, l2_idx, l2),
              "right": _pt(ctx, l3_idx, l3), "higher_low_pct": round(hl * 100, 2),
              "depth_pct": round(depth * 100, 2), "_anchor_price": l2}
    return _mk(ctx, "inverse_hs", "bullish", status, neck, neck + (neck - l2),
               l2, l2_idx, l1_idx, points, quality)


def _det_hs_top(ctx):
    """Mirror image: rally → high → equal-or-higher high (head) → LOWER high
    → neckline break down."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    min_lh, min_depth = 0.020 * s, 0.06 * s
    phighs = ctx["phighs"]
    if len(phighs) < 3:
        return None
    h3_idx, h3 = phighs[-1]
    if (n - 1) - h3_idx > cfg["recent"]:
        return None
    win_start = max(0, h3_idx - cfg["max_width"])
    head_cands = [(i, p) for i, p in phighs if win_start <= i <= h3_idx - cfg["min_sep"]]
    if not head_cands:
        return None
    h2_idx, h2 = max(head_cands, key=lambda t: t[1])
    if h3 > h2 * (1 - min_lh):
        return None  # no lower high
    highs_after = [x for x in ctx["highs"][h2_idx + 1:] if x is not None]
    if not highs_after or max(highs_after) > h2 * 1.001:
        return None
    ls_cands = [(i, p) for i, p in phighs if win_start <= i <= h2_idx - cfg["min_sep"]]
    if not ls_cands:
        return None
    h1_idx, h1 = max(ls_cands, key=lambda t: t[1])
    neck = _robust_extreme(ctx["lows"][h2_idx:h3_idx + 1], "low")
    if not neck or neck <= 0 or h3 <= neck * 1.005 or h1 <= neck:
        return None
    depth = (h2 - neck) / h2
    if depth < min_depth:
        return None
    pre = [x for x in ctx["lows"][max(0, h1_idx - (h3_idx - h1_idx)):h1_idx] if x is not None]
    if not pre or min(pre) > neck * (1 - 0.03 * s):
        return None  # didn't come UP into this
    if ctx["last"] >= h3:
        return None  # lower high already violated
    status = _status(ctx, h3_idx, neck, "bearish")
    if status is None:
        return None
    lh = (h2 - h3) / h2
    quality = min(10.0, 5.0 * depth / min_depth) + min(10.0, 6.0 * lh / min_lh)
    lw, rw = h2_idx - h1_idx, h3_idx - h2_idx
    if lw > 0 and 0.4 <= rw / lw <= 2.5:
        quality += 5.0
    points = {"left": _pt(ctx, h1_idx, h1), "head": _pt(ctx, h2_idx, h2),
              "right": _pt(ctx, h3_idx, h3), "lower_high_pct": round(lh * 100, 2),
              "depth_pct": round(depth * 100, 2), "_anchor_price": h2}
    return _mk(ctx, "hs_top", "bearish", status, neck, neck - (h2 - neck),
               h2, h2_idx, h1_idx, points, quality)


# ── Reversals: double bottom / double top ────────────────────────────────────

def _det_double_bottom(ctx):
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    tol, min_depth = 0.020 * s, 0.06 * s
    plows = ctx["plows"]
    if len(plows) < 2:
        return None
    l2_idx, l2 = plows[-1]
    if (n - 1) - l2_idx > cfg["recent"]:
        return None
    win_start = max(0, l2_idx - cfg["max_width"])
    cands = [(i, p) for i, p in plows
             if win_start <= i <= l2_idx - cfg["min_sep"] * 2
             and abs(l2 - p) / p <= tol]
    if not cands:
        return None
    l1_idx, l1 = cands[-1]  # nearest qualifying twin low
    bottom = min(l1, l2)
    lows_span = [x for x in ctx["lows"][l1_idx:] if x is not None]
    if not lows_span or min(lows_span) < bottom * 0.999:
        return None
    trigger = _robust_extreme(ctx["highs"][l1_idx:l2_idx + 1], "high")
    if not trigger:
        return None
    depth = (trigger - bottom) / bottom
    if depth < min_depth:
        return None
    pre = [x for x in ctx["highs"][max(0, l1_idx - (l2_idx - l1_idx)):l1_idx] if x is not None]
    if not pre or max(pre) < trigger * (1 + 0.04 * s):
        return None  # needs a real decline INTO the lows, not sideways chop
    # The second low must have BOUNCED (≥25% of pattern height) — until it
    # does you can't call it a double bottom, and this also stops flat-top
    # triangles / plain chop from masquerading as one.
    if ctx["last"] < bottom + 0.25 * (trigger - bottom):
        return None
    status = _status(ctx, l2_idx, trigger, "bullish")
    if status is None:
        return None
    closeness = abs(l2 - l1) / l1
    quality = min(12.0, 6.0 * depth / min_depth) + max(0.0, 8.0 * (1 - closeness / tol)) \
        + (5.0 if l2_idx - l1_idx >= cfg["min_sep"] * 3 else 2.0)
    points = {"low1": _pt(ctx, l1_idx, l1), "low2": _pt(ctx, l2_idx, l2),
              "depth_pct": round(depth * 100, 2), "_anchor_price": bottom}
    return _mk(ctx, "double_bottom", "bullish", status, trigger,
               trigger + (trigger - bottom), bottom, l2_idx, l1_idx, points, quality)


def _det_double_top(ctx):
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    tol, min_depth = 0.020 * s, 0.06 * s
    phighs = ctx["phighs"]
    if len(phighs) < 2:
        return None
    h2_idx, h2 = phighs[-1]
    if (n - 1) - h2_idx > cfg["recent"]:
        return None
    win_start = max(0, h2_idx - cfg["max_width"])
    cands = [(i, p) for i, p in phighs
             if win_start <= i <= h2_idx - cfg["min_sep"] * 2
             and abs(h2 - p) / p <= tol]
    if not cands:
        return None
    h1_idx, h1 = cands[-1]
    top = max(h1, h2)
    highs_span = [x for x in ctx["highs"][h1_idx:] if x is not None]
    if not highs_span or max(highs_span) > top * 1.001:
        return None
    trigger = _robust_extreme(ctx["lows"][h1_idx:h2_idx + 1], "low")
    if not trigger or trigger <= 0:
        return None
    depth = (top - trigger) / top
    if depth < min_depth:
        return None
    pre = [x for x in ctx["lows"][max(0, h1_idx - (h2_idx - h1_idx)):h1_idx] if x is not None]
    if not pre or min(pre) > trigger * (1 - 0.04 * s):
        return None  # needs a real rally INTO the highs, not sideways chop
    # The second high must have been REJECTED (≥25% of pattern height) —
    # otherwise a flat-bottom triangle or chop reads as a double top.
    if ctx["last"] > top - 0.25 * (top - trigger):
        return None
    status = _status(ctx, h2_idx, trigger, "bearish")
    if status is None:
        return None
    closeness = abs(h2 - h1) / h1
    quality = min(12.0, 6.0 * depth / min_depth) + max(0.0, 8.0 * (1 - closeness / tol)) \
        + (5.0 if h2_idx - h1_idx >= cfg["min_sep"] * 3 else 2.0)
    points = {"high1": _pt(ctx, h1_idx, h1), "high2": _pt(ctx, h2_idx, h2),
              "depth_pct": round(depth * 100, 2), "_anchor_price": top}
    return _mk(ctx, "double_top", "bearish", status, trigger,
               trigger - (top - trigger), top, h2_idx, h1_idx, points, quality)


# ── Continuations: flags ─────────────────────────────────────────────────────
# Flags are listed while FORMING (the pole high/low is the trigger); the
# intraday alert catches the actual break, and the next nightly run drops
# the row once it's no longer a live flag.

def _det_bull_flag(ctx):
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    min_run = 0.18 * s
    fm, rl = cfg["flag_max"], cfg["run_len"]
    lo = max(0, n - 1 - fm - rl)
    seg = ctx["highs"][lo:]
    vals = [(i + lo, v) for i, v in enumerate(seg) if v is not None]
    if not vals:
        return None
    h_idx, pole_high = max(vals, key=lambda t: t[1])
    flag_bars = (n - 1) - h_idx
    if not (3 <= flag_bars <= fm) or h_idx < 3:
        return None
    run_lows = [x for x in ctx["lows"][max(0, h_idx - rl):h_idx + 1] if x is not None]
    if not run_lows:
        return None
    run_low = min(run_lows)
    if run_low <= 0:
        return None
    run = (pole_high - run_low) / run_low
    if run < min_run:
        return None
    flag_lows = [x for x in ctx["lows"][h_idx + 1:] if x is not None]
    if not flag_lows:
        return None
    flag_low = min(flag_lows)
    retrace = (pole_high - flag_low) / (pole_high - run_low)
    if retrace > 0.5 or ctx["last"] <= flag_low:
        return None
    quality = min(12.0, 6.0 * run / min_run) + 16.0 * max(0.0, 0.5 - retrace)
    points = {"pole_low": round(run_low, 4), "pole_high": _pt(ctx, h_idx, pole_high),
              "flag_low": round(flag_low, 4), "run_pct": round(run * 100, 2),
              "retrace_pct": round(retrace * 100, 1), "_anchor_price": pole_high}
    return _mk(ctx, "bull_flag", "bullish", "forming", pole_high,
               flag_low + (pole_high - run_low), flag_low, h_idx,
               max(0, h_idx - rl), points, quality)


def _det_bear_flag(ctx):
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    min_run = 0.18 * s
    fm, rl = cfg["flag_max"], cfg["run_len"]
    lo = max(0, n - 1 - fm - rl)
    seg = ctx["lows"][lo:]
    vals = [(i + lo, v) for i, v in enumerate(seg) if v is not None]
    if not vals:
        return None
    l_idx, pole_low = min(vals, key=lambda t: t[1])
    flag_bars = (n - 1) - l_idx
    if not (3 <= flag_bars <= fm) or l_idx < 3 or pole_low <= 0:
        return None
    run_highs = [x for x in ctx["highs"][max(0, l_idx - rl):l_idx + 1] if x is not None]
    if not run_highs:
        return None
    run_high = max(run_highs)
    run = (run_high - pole_low) / pole_low
    if run < min_run:
        return None
    flag_highs = [x for x in ctx["highs"][l_idx + 1:] if x is not None]
    if not flag_highs:
        return None
    flag_high = max(flag_highs)
    retrace = (flag_high - pole_low) / (run_high - pole_low)
    if retrace > 0.5 or ctx["last"] >= flag_high:
        return None
    quality = min(12.0, 6.0 * run / min_run) + 16.0 * max(0.0, 0.5 - retrace)
    points = {"pole_high": round(run_high, 4), "pole_low": _pt(ctx, l_idx, pole_low),
              "flag_high": round(flag_high, 4), "run_pct": round(run * 100, 2),
              "retrace_pct": round(retrace * 100, 1), "_anchor_price": pole_low}
    return _mk(ctx, "bear_flag", "bearish", "forming", pole_low,
               flag_high - (run_high - pole_low), flag_high, l_idx,
               max(0, l_idx - rl), points, quality)


# ── Continuations: triangles ─────────────────────────────────────────────────

def _det_asc_triangle(ctx):
    """Flat resistance tested 2+ times, rising lows squeezing into it."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    flat_tol, rise_min = 0.015 * s, 0.008 * s
    start = max(cfg["pivot_k"], n - cfg["tri_window"])
    phs = [(i, p) for i, p in ctx["phighs"] if i >= start]
    pls = [(i, p) for i, p in ctx["plows"] if i >= start]
    if len(phs) < 2 or len(pls) < 2:
        return None
    r = max(p for _, p in phs)
    touches = [(i, p) for i, p in phs if p >= r * (1 - flat_tol)]
    if len(touches) < 2:
        return None
    lows_in = [(i, p) for i, p in pls if i >= touches[0][0]]
    if len(lows_in) < 2:
        return None
    if not all(lows_in[j + 1][1] >= lows_in[j][1] * (1 + rise_min)
               for j in range(len(lows_in) - 1)):
        return None
    first_low, last_low = lows_in[0][1], lows_in[-1][1]
    if last_low >= r or first_low <= 0:
        return None
    if (r - last_low) > (r - first_low) * 0.75:
        return None  # lows not actually squeezing into the resistance
    if (n - 1) - max(touches[-1][0], lows_in[-1][0]) > cfg["recent"]:
        return None
    if ctx["last"] <= last_low:
        return None
    status = _status(ctx, lows_in[-1][0], r, "bullish")
    if status is None:
        return None
    contraction = (r - last_low) / (r - first_low)
    quality = min(10.0, 4.0 * len(touches)) + 12.0 * max(0.0, 0.75 - contraction)
    points = {"resistance": round(r, 4), "touches": len(touches),
              "first_low": round(first_low, 4), "last_low": round(last_low, 4),
              "_anchor_price": r}
    return _mk(ctx, "asc_triangle", "bullish", status, r, r + (r - first_low),
               last_low, touches[0][0], start, points, quality)


def _det_desc_triangle(ctx):
    """Flat support tested 2+ times, falling highs pressing into it."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    flat_tol, fall_min = 0.015 * s, 0.008 * s
    start = max(cfg["pivot_k"], n - cfg["tri_window"])
    phs = [(i, p) for i, p in ctx["phighs"] if i >= start]
    pls = [(i, p) for i, p in ctx["plows"] if i >= start]
    if len(phs) < 2 or len(pls) < 2:
        return None
    sup = min(p for _, p in pls)
    if sup <= 0:
        return None
    touches = [(i, p) for i, p in pls if p <= sup * (1 + flat_tol)]
    if len(touches) < 2:
        return None
    highs_in = [(i, p) for i, p in phs if i >= touches[0][0]]
    if len(highs_in) < 2:
        return None
    if not all(highs_in[j + 1][1] <= highs_in[j][1] * (1 - fall_min)
               for j in range(len(highs_in) - 1)):
        return None
    first_high, last_high = highs_in[0][1], highs_in[-1][1]
    if last_high <= sup:
        return None
    if (last_high - sup) > (first_high - sup) * 0.75:
        return None
    if (n - 1) - max(touches[-1][0], highs_in[-1][0]) > cfg["recent"]:
        return None
    if ctx["last"] >= last_high:
        return None
    status = _status(ctx, highs_in[-1][0], sup, "bearish")
    if status is None:
        return None
    contraction = (last_high - sup) / (first_high - sup)
    quality = min(10.0, 4.0 * len(touches)) + 12.0 * max(0.0, 0.75 - contraction)
    points = {"support": round(sup, 4), "touches": len(touches),
              "first_high": round(first_high, 4), "last_high": round(last_high, 4),
              "_anchor_price": sup}
    return _mk(ctx, "desc_triangle", "bearish", status, sup, sup - (first_high - sup),
               last_high, touches[0][0], start, points, quality)


# ── Wedges ───────────────────────────────────────────────────────────────────

def _det_falling_wedge(ctx):
    """3+ falling highs over 2+ falling lows, range contracting — bullish."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    step_min, decline_min = 0.004 * s, 0.05 * s
    start = max(cfg["pivot_k"], n - cfg["tri_window"])
    phs = [(i, p) for i, p in ctx["phighs"] if i >= start]
    pls = [(i, p) for i, p in ctx["plows"] if i >= start]
    if len(phs) < 3:
        return None
    # The wedge's lows live INSIDE it — a stray pivot low before the first
    # high (the top of the structure) would poison the range pairing.
    pls = [x for x in pls if x[0] > phs[0][0]]
    if len(pls) < 2:
        return None
    if not all(phs[j + 1][1] <= phs[j][1] * (1 - step_min) for j in range(len(phs) - 1)):
        return None
    if not all(pls[j + 1][1] <= pls[j][1] for j in range(len(pls) - 1)):
        return None
    first_range = phs[0][1] - pls[0][1]
    last_range = phs[-1][1] - pls[-1][1]
    if first_range <= 0 or last_range <= 0 or last_range > first_range * 0.65:
        return None  # not converging
    if phs[-1][1] > phs[0][1] * (1 - decline_min):
        return None  # not enough of a decline to reverse
    if (n - 1) - phs[-1][0] > cfg["recent"] and (n - 1) - pls[-1][0] > cfg["recent"]:
        return None
    trigger = phs[-1][1]
    if ctx["last"] <= pls[-1][1]:
        return None
    status = _status(ctx, phs[-1][0], trigger, "bullish")
    if status is None:
        return None
    quality = min(10.0, 3.0 * (len(phs) + len(pls) - 4)) \
        + 12.0 * max(0.0, 0.65 - last_range / first_range)
    points = {"first_high": _pt(ctx, phs[0][0], phs[0][1]),
              "last_high": _pt(ctx, phs[-1][0], phs[-1][1]),
              "last_low": round(pls[-1][1], 4), "_anchor_price": pls[-1][1]}
    return _mk(ctx, "falling_wedge", "bullish", status, trigger, phs[0][1],
               pls[-1][1], pls[-1][0], start, points, quality)


def _det_rising_wedge(ctx):
    """3+ rising lows under 2+ rising highs, range contracting — bearish."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    step_min, climb_min = 0.004 * s, 0.05 * s
    start = max(cfg["pivot_k"], n - cfg["tri_window"])
    phs = [(i, p) for i, p in ctx["phighs"] if i >= start]
    pls = [(i, p) for i, p in ctx["plows"] if i >= start]
    if len(pls) < 3:
        return None
    # Mirror of the falling wedge: highs must sit INSIDE the structure.
    phs = [x for x in phs if x[0] > pls[0][0]]
    if len(phs) < 2:
        return None
    if not all(pls[j + 1][1] >= pls[j][1] * (1 + step_min) for j in range(len(pls) - 1)):
        return None
    if not all(phs[j + 1][1] >= phs[j][1] for j in range(len(phs) - 1)):
        return None
    first_range = phs[0][1] - pls[0][1]
    last_range = phs[-1][1] - pls[-1][1]
    if first_range <= 0 or last_range <= 0 or last_range > first_range * 0.65:
        return None
    if pls[0][1] <= 0 or pls[-1][1] < pls[0][1] * (1 + climb_min):
        return None
    if (n - 1) - phs[-1][0] > cfg["recent"] and (n - 1) - pls[-1][0] > cfg["recent"]:
        return None
    trigger = pls[-1][1]
    if ctx["last"] >= phs[-1][1]:
        return None
    status = _status(ctx, pls[-1][0], trigger, "bearish")
    if status is None:
        return None
    quality = min(10.0, 3.0 * (len(phs) + len(pls) - 4)) \
        + 12.0 * max(0.0, 0.65 - last_range / first_range)
    points = {"first_low": _pt(ctx, pls[0][0], pls[0][1]),
              "last_low": _pt(ctx, pls[-1][0], pls[-1][1]),
              "last_high": round(phs[-1][1], 4), "_anchor_price": phs[-1][1]}
    return _mk(ctx, "rising_wedge", "bearish", status, trigger, pls[0][1],
               phs[-1][1], pls[-1][0], start, points, quality)


# ── Cup & handle ─────────────────────────────────────────────────────────────

def _det_cup_handle(ctx):
    """Rounded base between two rims at the same level, then a shallow handle
    pause under the rim — breakout over the rim completes it."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    rim_tol, min_depth, max_depth = 0.04 * s, 0.10 * s, 0.55
    phighs = ctx["phighs"]
    # Right rim: latest pivot high with a 3..handle_max bar handle after it.
    cands = [(i, p) for i, p in phighs if 3 <= (n - 1) - i <= cfg["handle_max"]]
    if not cands:
        return None
    r_idx, r_rim = cands[-1]
    # Left rim: an earlier pivot high at the same level, a real cup-width away.
    lcands = [(i, p) for i, p in phighs
              if r_idx - cfg["cup_max"] <= i <= r_idx - cfg["cup_min"]
              and abs(p - r_rim) / r_rim <= rim_tol]
    if not lcands:
        return None
    l_idx, l_rim = lcands[0]
    rim = max(l_rim, r_rim)
    inner_h = [x for x in ctx["highs"][l_idx + 1:r_idx] if x is not None]
    if not inner_h or max(inner_h) > rim * 1.015:
        return None  # the rims must be the top of the cup
    inner = [(j, x) for j, x in enumerate(ctx["lows"][l_idx + 1:r_idx]) if x is not None]
    if not inner:
        return None
    b_off, bottom = min(inner, key=lambda t: t[1])
    depth = (rim - bottom) / rim
    if not (min_depth <= depth <= max_depth):
        return None
    b_rel = (b_off + 1) / (r_idx - l_idx)
    if not (0.2 <= b_rel <= 0.85):
        return None  # V-low hugging one rim — not a rounded base
    # Handle: a shallow pause in the UPPER half of the cup.
    h_lows = [x for x in ctx["lows"][r_idx + 1:] if x is not None]
    if not h_lows:
        return None
    h_low = min(h_lows)
    if h_low < bottom + 0.55 * (rim - bottom):
        return None  # handle too deep — that's just the cup refilling
    if ctx["last"] <= h_low:
        return None
    status = _status(ctx, r_idx, rim, "bullish")
    if status is None:
        return None
    handle_ret = (rim - h_low) / (rim - bottom) if rim > bottom else 1.0
    quality = min(8.0, 20.0 * depth) \
        + (9.0 if handle_ret <= 0.25 else (5.0 if handle_ret <= 0.40 else 2.0)) \
        + 5.0 * max(0.0, 1.0 - abs(l_rim - r_rim) / r_rim / rim_tol)
    points = {"left_rim": _pt(ctx, l_idx, l_rim), "right_rim": _pt(ctx, r_idx, r_rim),
              "bottom": _pt(ctx, l_idx + 1 + b_off, bottom),
              "handle_low": round(h_low, 4), "depth_pct": round(depth * 100, 2),
              "_anchor_price": bottom}
    return _mk(ctx, "cup_handle", "bullish", status, rim, rim + (rim - bottom),
               h_low, l_idx + 1 + b_off, l_idx, points, quality)


# ── Long-term range breakout / breakdown ─────────────────────────────────────

def _det_range_break(ctx):
    """A horizontal range tested repeatedly on BOTH edges for a long time,
    with price now at (or freshly through) an edge. Tries the longest
    qualifying window first — the longer the range, the bigger the move it
    tends to fuel. Mid-range names are skipped: nothing actionable."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    closes, last = ctx["closes"], ctx["last"]
    for L in cfg["range_lens"]:
        if L > n - 2:
            continue
        start = n - L
        hi = _robust_extreme(ctx["highs"][start:], "high")
        lo = _robust_extreme(ctx["lows"][start:], "low")
        if not hi or not lo or lo <= 0 or hi <= lo:
            continue
        height = (hi - lo) / lo
        if height > 0.28 * s or height < 0.04:
            continue  # not a tradeable box: too loose to be a range, or dead
        cs = [c for c in closes[start:] if c is not None]
        if len(cs) < L * 0.8:
            continue
        band = 0.2 * (hi - lo)
        t_top = sum(1 for c in cs if c >= hi - band)
        t_bot = sum(1 for c in cs if c <= lo + band)
        if t_top < 4 or t_bot < 4:
            continue  # both edges must be well-tested
        # A steady trend "touches" its window's bottom early and top late and
        # would masquerade as a range — real ranges OSCILLATE, so both edges
        # must be visited in both halves of the window.
        half = len(cs) // 2
        if not all(any(c >= hi - band for c in part) and
                   any(c <= lo + band for c in part)
                   for part in (cs[:half], cs[half:])):
            continue

        quality = min(8.0, 4.0 * L / cfg["range_lens"][-1]) \
            + min(9.0, 0.75 * min(t_top, t_bot)) \
            + (5.0 if height <= 0.18 * s else 0.0)
        points = {"range_high": round(hi, 4), "range_low": round(lo, 4),
                  "bars": L, "height_pct": round(height * 100, 2),
                  "touches_top": t_top, "touches_bottom": t_bot}
        mid = (hi + lo) / 2

        if last >= hi:
            below = [i for i in range(start, n)
                     if closes[i] is not None and closes[i] < hi]
            if not below:
                continue
            since = (n - 1) - max(below)
            if since > cfg["break_recent"] or last > hi * (1 + cfg["max_ext"]):
                continue  # stale or extended break
            points["_anchor_price"] = hi
            return _mk(ctx, "range_breakout", "bullish", "breakout", hi,
                       hi + (hi - lo), mid, start, start, points, quality)
        if last <= lo:
            above = [i for i in range(start, n)
                     if closes[i] is not None and closes[i] > lo]
            if not above:
                continue
            since = (n - 1) - max(above)
            if since > cfg["break_recent"] or last < lo * (1 - cfg["max_ext"]):
                continue
            points["_anchor_price"] = lo
            return _mk(ctx, "range_breakdown", "bearish", "breakout", lo,
                       lo - (hi - lo), mid, start, start, points, quality)
        if last > hi - (hi - lo) * 0.25:
            points["_anchor_price"] = hi
            return _mk(ctx, "range_breakout", "bullish", "forming", hi,
                       hi + (hi - lo), mid, start, start, points, quality)
        if last < lo + (hi - lo) * 0.25:
            points["_anchor_price"] = lo
            return _mk(ctx, "range_breakdown", "bearish", "forming", lo,
                       lo - (hi - lo), mid, start, start, points, quality)
    return None


DETECTORS = [_det_inverse_hs, _det_hs_top, _det_double_bottom, _det_double_top,
             _det_bull_flag, _det_bear_flag, _det_asc_triangle,
             _det_desc_triangle, _det_falling_wedge, _det_rising_wedge,
             _det_cup_handle, _det_range_break]


def detect_patterns(bars: list, timeframe: str) -> list:
    """Run every detector over one series. bars: dicts with date/high/low/
    close/volume, oldest → newest. Returns [] when nothing is live."""
    if timeframe not in TF:
        return []
    ctx = _ctx(bars, timeframe)
    if ctx is None:
        return []
    out = []
    for det in DETECTORS:
        try:
            r = det(ctx)
            if r:
                r["timeframe"] = timeframe
                out.append(r)
        except Exception as e:
            log.debug(f"[patterns] detector {det.__name__} error: {e}")
    return out


# ── DB scan (weekly + daily) ─────────────────────────────────────────────────

def _universe(conn) -> list:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker FROM screener_snapshot
            UNION
            SELECT ticker FROM watchlist WHERE active = true
        """)
        return sorted({r[0] for r in cur.fetchall() if r[0]})


# daily_prices stores close + volume only (no OHLC), so the weekly and daily
# timeframes detect CLOSING-PRICE structure — necklines, shoulders, and
# triggers on closes, the way a line chart draws them. That's a standard
# charting basis and immune to single-print wick noise; the 4h timeframe
# (Polygon bars) uses true highs/lows.

def _rows_to_bars(rows) -> dict:
    out: dict = {}
    for t, d, hi, lo, c, v in rows:
        c = float(c) if c is not None else None
        out.setdefault(t, []).append({
            "date": d,
            "high": float(hi) if hi is not None else c,
            "low": float(lo) if lo is not None else c,
            "close": c,
            "volume": float(v or 0)})
    return out


def _fetch_daily(conn, tickers: list) -> dict:
    """~1y of close-basis daily bars per ticker, oldest → newest."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, trade_date, close, close, close, volume
            FROM daily_prices
            WHERE ticker = ANY(%s) AND trade_date >= CURRENT_DATE - 400
            ORDER BY ticker, trade_date
        """, (tickers,))
        return _rows_to_bars(cur.fetchall())


def _fetch_weekly(conn, tickers: list) -> dict:
    """~3y of weekly bars aggregated in SQL (close-basis highs/lows), oldest
    → newest. The current (partial) week rides along as the live bar."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, date_trunc('week', trade_date)::date AS wk,
                   max(close), min(close),
                   (array_agg(close ORDER BY trade_date DESC))[1],
                   sum(volume)
            FROM daily_prices
            WHERE ticker = ANY(%s) AND trade_date >= CURRENT_DATE - 1120
            GROUP BY ticker, date_trunc('week', trade_date)
            ORDER BY ticker, wk
        """, (tickers,))
        return _rows_to_bars(cur.fetchall())


def _upsert_rows(conn, rows: list, timeframe: str, run_started) -> None:
    """Replace a timeframe's results: upsert what this run found, then drop
    what it no longer sees. detected_at survives while the anchor bar (the
    pattern's defining extreme) is unchanged."""
    with conn.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO pattern_scan
                    (ticker, timeframe, pattern, direction, status,
                     trigger_price, target, invalid_level, anchor_price,
                     anchor_date, points, last_close, dist_to_trigger_pct,
                     score, detected_at, scanned_at)
                VALUES (%(ticker)s, %(timeframe)s, %(pattern)s, %(direction)s, %(status)s,
                        %(trigger_price)s, %(target)s, %(invalid_level)s, %(anchor_price)s,
                        %(anchor_date)s, %(points_json)s::jsonb, %(last_close)s,
                        %(dist_to_trigger_pct)s, %(score)s, now(), now())
                ON CONFLICT (ticker, timeframe, pattern) DO UPDATE SET
                    direction = EXCLUDED.direction,
                    status = EXCLUDED.status,
                    trigger_price = EXCLUDED.trigger_price,
                    target = EXCLUDED.target,
                    invalid_level = EXCLUDED.invalid_level,
                    anchor_price = EXCLUDED.anchor_price,
                    anchor_date = EXCLUDED.anchor_date,
                    points = EXCLUDED.points,
                    last_close = EXCLUDED.last_close,
                    dist_to_trigger_pct = EXCLUDED.dist_to_trigger_pct,
                    score = EXCLUDED.score,
                    detected_at = CASE
                        WHEN pattern_scan.anchor_date = EXCLUDED.anchor_date
                        THEN pattern_scan.detected_at ELSE now() END,
                    scanned_at = now()
            """, {**r, "points_json": json.dumps(r.get("points") or {}, default=str)})
        cur.execute("DELETE FROM pattern_scan WHERE timeframe = %s AND scanned_at < %s",
                    (timeframe, run_started))
    conn.commit()


def scan_db_timeframes() -> dict:
    """Weekly + daily scan over the screener universe + watchlist, straight
    from daily_prices. Returns {'weekly': [tickers], 'daily': [tickers]}."""
    from screen.reversal_screen import _conn
    from datetime import datetime, timezone
    conn = _conn()
    found = {"weekly": [], "daily": []}
    try:
        run_started = datetime.now(timezone.utc)
        tickers = _universe(conn)
        log.info(f"[patterns] daily/weekly scan over {len(tickers)} names")
        rows_daily, rows_weekly = [], []
        for i in range(0, len(tickers), 250):
            batch = tickers[i:i + 250]
            daily_map = _fetch_daily(conn, batch)
            weekly_map = _fetch_weekly(conn, batch)
            for t in batch:
                for r in detect_patterns(daily_map.get(t) or [], "daily"):
                    r["ticker"] = t
                    rows_daily.append(r)
                for r in detect_patterns(weekly_map.get(t) or [], "weekly"):
                    r["ticker"] = t
                    rows_weekly.append(r)
        _upsert_rows(conn, rows_daily, "daily", run_started)
        _upsert_rows(conn, rows_weekly, "weekly", run_started)
        found["daily"] = sorted({r["ticker"] for r in rows_daily})
        found["weekly"] = sorted({r["ticker"] for r in rows_weekly})
        log.info(f"[patterns] daily: {len(rows_daily)} patterns on "
                 f"{len(found['daily'])} names; weekly: {len(rows_weekly)} on "
                 f"{len(found['weekly'])}")
    finally:
        conn.close()
    return found


# ── 4h scan (Polygon, bounded candidate set) ─────────────────────────────────

def _four_h_candidates(conn, hits: dict) -> list:
    cands = set(hits.get("daily") or []) | set(hits.get("weekly") or [])
    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM watchlist WHERE active = true")
        cands.update(r[0] for r in cur.fetchall() if r[0])
        cur.execute("SELECT DISTINCT ticker FROM pattern_scan WHERE timeframe = '4h'")
        cands.update(r[0] for r in cur.fetchall() if r[0])
        cur.execute("""
            SELECT ticker FROM daily_prices
            WHERE trade_date >= CURRENT_DATE - 30
            GROUP BY ticker
            ORDER BY avg(close * volume) DESC NULLS LAST
            LIMIT %s
        """, (FOUR_H_LIQUID_TOP,))
        cands.update(r[0] for r in cur.fetchall() if r[0])
    return sorted(cands)[:FOUR_H_MAX_CANDIDATES]


def scan_4h(hits: dict = None) -> int:
    """4-hour scan over watchlist + weekly/daily hits + the most liquid names.
    One Polygon call per candidate (same bars the levels engine uses)."""
    from screen.reversal_screen import _conn
    from analysis.polygon_data import fetch_recent_bars
    from datetime import datetime, timezone

    conn = _conn()
    try:
        run_started = datetime.now(timezone.utc)
        cands = _four_h_candidates(conn, hits or {})
        log.info(f"[patterns] 4h scan over {len(cands)} candidates")

        def _one(t):
            bars = fetch_recent_bars(t, days=120, multiplier=4, timespan="hour")
            found = detect_patterns(bars or [], "4h")
            for r in found:
                r["ticker"] = t
            return found

        rows = []
        with ThreadPoolExecutor(max_workers=FOUR_H_WORKERS) as ex:
            futs = {ex.submit(_one, t): t for t in cands}
            for f in as_completed(futs):
                try:
                    rows.extend(f.result())
                except Exception as e:
                    log.warning(f"[patterns] 4h {futs[f]} failed: {e}")
        _upsert_rows(conn, rows, "4h", run_started)
        log.info(f"[patterns] 4h: {len(rows)} patterns")
        return len(rows)
    finally:
        conn.close()


def run_pattern_scan(include_4h: bool = True) -> dict:
    """Full scan: weekly + daily from the DB, then 4h via Polygon."""
    hits = scan_db_timeframes()
    n4 = scan_4h(hits) if include_4h else 0
    return {"weekly": len(hits["weekly"]), "daily": len(hits["daily"]), "4h": n4}


# ── Intraday breakout alerts ─────────────────────────────────────────────────
# Forming patterns' trigger lines checked against live prices each scan — the
# moment a name breaks its neckline/flag/triangle you get a signal row
# (dashboard, notification, email gating, alert_log) instead of finding out
# at the next nightly rescan. Same shape as the watchlist × levels alerts.

_forming_cache: tuple = (0.0, {})   # (fetched_at, {ticker: [pattern dicts]})
_FORMING_TTL = 600
_pb_last_price: dict = {}           # ticker -> previous scan's price
_pb_alerted: dict = {}              # (ticker, timeframe, pattern) -> epoch
_PB_COOLDOWN = 12 * 3600

_TF_SCORE = {"weekly": 80.0, "daily": 75.0, "4h": 65.0}
_TF_LABEL = {"weekly": "weekly", "daily": "daily", "4h": "4H"}


def _forming_patterns() -> dict:
    global _forming_cache
    now = time.time()
    if now - _forming_cache[0] < _FORMING_TTL:
        return _forming_cache[1]
    out: dict = {}
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ticker, timeframe, pattern, direction,
                           trigger_price, target, score
                    FROM pattern_scan WHERE status = 'forming'
                """)
                for t, tf, pat, direction, trig, tgt, score in cur.fetchall():
                    out.setdefault(t, []).append({
                        "timeframe": tf, "pattern": pat, "direction": direction,
                        "trigger": float(trig), "target": float(tgt),
                        "score": float(score or 0)})
        finally:
            conn.close()
        _forming_cache = (now, out)
    except Exception as e:
        log.warning(f"[patterns] forming-pattern load failed: {e}")
    return out


def build_pattern_breakout_alerts() -> list:
    """Signal-shaped rows for forming patterns whose trigger was crossed
    since the previous scan — up through it for bullish patterns
    (PATTERN_BREAKOUT), down through it for bearish ones (PATTERN_BREAKDOWN).
    Returns [] when nothing broke."""
    forming = _forming_patterns()
    if not forming:
        return []
    tickers = sorted(forming)
    try:
        from analysis.news_scanner import _fetch_snapshot_map
        snap_map = _fetch_snapshot_map(tickers)
    except Exception as e:
        log.warning(f"[patterns] snapshot fetch failed: {e}")
        return []

    now = time.time()
    rows = []
    for ticker in tickers:
        snap = snap_map.get(ticker) or {}
        price = float(snap.get("price") or 0)
        if price <= 0:
            continue
        prev = _pb_last_price.get(ticker)
        _pb_last_price[ticker] = price
        if prev is None or prev <= 0 or prev == price:
            continue  # first sighting (baseline only) or no movement

        for pat in forming[ticker]:
            trig = pat["trigger"]
            bullish = pat["direction"] == "bullish"
            crossed = (prev < trig <= price) if bullish else (price <= trig < prev)
            if not crossed:
                continue
            key = (ticker, pat["timeframe"], pat["pattern"])
            if now - _pb_alerted.get(key, 0) < _PB_COOLDOWN:
                continue
            _pb_alerted[key] = now

            tf = _TF_LABEL.get(pat["timeframe"], pat["timeframe"])
            name = PATTERN_NAMES.get(pat["pattern"], pat["pattern"])
            move = (pat["target"] - price) / price * 100.0
            verb = "broke above trigger" if bullish else "broke below trigger"
            rows.append({
                "ticker": ticker,
                "sleeve": "pattern",
                "signal_type": "PATTERN_BREAKOUT" if bullish else "PATTERN_BREAKDOWN",
                "score": _TF_SCORE.get(pat["timeframe"], 70.0),
                "rationale": (f"{name} ({tf}): {verb} ${trig:,.2f} — "
                              f"target ${pat['target']:,.2f} ({move:+.1f}%)")[:200],
                "current_price": price,
                "change_pct": round(float(snap.get("change_pct") or 0), 2),
                "vol_pace_ratio": round(float(snap.get("vol_ratio") or 0), 2),
                "today_volume": int(snap.get("volume") or 0),
                "gap_pct": 0.0,
                "above_vwap": bullish,
                "pattern": pat["pattern"],
                "pattern_timeframe": pat["timeframe"],
                "company_name": "",
                "sector": "",
            })
            _mark_breakout(ticker, pat["timeframe"], pat["pattern"])
    return rows


def _mark_breakout(ticker: str, timeframe: str, pattern: str) -> None:
    """Flip the DB row live so the dashboard shows 'breakout' immediately;
    the nightly rescan re-derives it from bars anyway."""
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE pattern_scan SET status = 'breakout' "
                            "WHERE ticker = %s AND timeframe = %s AND pattern = %s",
                            (ticker, timeframe, pattern))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"[patterns] breakout mark failed for {ticker}: {e}")
