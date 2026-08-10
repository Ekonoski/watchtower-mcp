"""
Watchtower — classical chart-pattern scanner (weekly / daily / 4h).

One shared pivot engine (fractal swing highs/lows, same primitive as
analysis/levels.py) feeding a family of detectors for the common setups:

  bullish reversals    inverse_hs, double_bottom, falling_wedge
  bearish reversals    hs_top, double_top, rising_wedge
  continuations        bull_flag, bear_flag, asc_triangle, desc_triangle

The flagship is the inverse head & shoulders — decline → low → clearly
LOWER low (head) → HIGHER low (right shoulder) → neckline break. The higher
low off the head is what proves the trend change. The head must sit a real
margin below BOTH shoulders: near-equal lows are a double bottom, and the
double-bottom detector owns that shape (each detector claims its own
geometry — KFY's twin $59 lows were being mislabeled as an iHS).

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

# Bump whenever detectors/thresholds change: the scheduler rescans once per
# version on deploy, so new/changed patterns populate within minutes instead
# of waiting for the next 6:45 AM slot.
# NOTE (2026-08-10): wma_touch shipped WITHOUT a bump, deliberately. A bump
# also invalidates the pattern-backtest marker (keyed v{BT}_e{ENGINE}) and
# would truncate + re-run the entire 55-minute replay on deploy — for a
# detector the replay's 420-day windows can't fire anyway (it needs 240
# weekly bars). wma_touch rows populate at the next 6:45 scan instead; the
# proper bump rides with the future replay-window extension that lets the
# harness grade it natively.
ENGINE_VERSION = 14   # v14: structure shift (higher low / lower high)

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
    "diamond_top": "Diamond Top", "diamond_bottom": "Diamond Bottom",
    "cup_handle": "Cup & Handle",
    "range_breakout": "Range Breakout", "range_breakdown": "Range Breakdown",
    "ema_bounce": "EMA 8/13 Bounce", "ema_reject": "EMA 8/13 Reject",
    "higher_low": "Higher Low", "lower_high": "Lower High",
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


def _plows_live(ctx):
    """Confirmed pivot lows PLUS one provisional recent low.

    A fractal pivot needs `pivot_k` bars after it to confirm, so the second
    low of a double bottom (or the right shoulder of an inverse H&S) is
    invisible for a week or two after it prints — exactly when the setup is
    most actionable. This adds the most recent bar that has already turned up
    (a local min over its left window, undercut by nothing since, price now
    bouncing above it) even though it lacks full right-side confirmation. The
    downstream depth / prior-decline / higher-low checks still gate it, and if
    a later bar undercuts it the next scan simply drops the pattern — so a
    provisional low can only surface a real setup earlier, never invent one."""
    plows = list(ctx["plows"])
    k, n, lows, last = ctx["cfg"]["pivot_k"], ctx["n"], ctx["lows"], ctx["last"]
    last_conf = plows[-1][0] if plows else -1
    best = None
    for i in range(max(last_conf + 1, k), n - 1):   # need >=1 bar after
        lo = lows[i]
        if lo is None:
            continue
        left = [x for x in lows[max(0, i - k):i] if x is not None]
        right = [x for x in lows[i + 1:] if x is not None]
        if not right or min(right) < lo or last <= lo:
            continue                                # undercut since, or no bounce
        if left and min(left) < lo:
            continue                                # not a local min vs its left
        best = (i, lo)                              # keep the most recent
    if best and best[0] > last_conf and (n - 1) - best[0] <= ctx["cfg"]["recent"]:
        plows.append(best)
    return plows


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


def _status(ctx, start_idx: int, trigger: float, direction: str, target=None):
    """'forming' / 'retest' / 'breakout' / None (stale or extended break —
    not listable). forming = price has never closed through the trigger;
    retest = it broke through after the anchor and has pulled back to the
    other side WITHOUT the measured move playing out (the throwback — a
    second-chance entry, and empirically where the best entries live:
    NU/AAL/SPGI all entered on this state while it was still labeled
    'forming'); breakout = crossed within break_recent bars and not
    extended. Spent patterns (target reached after the structure
    completed) are dropped — a finished trade, not an entry."""
    closes, n, cfg = ctx["closes"], ctx["n"], ctx["cfg"]
    last = ctx["last"]
    if target is not None:
        if direction == "bullish":
            hs = [h for h in ctx["highs"][start_idx + 1:] if h is not None]
            if hs and max(hs) >= target:
                return None
        else:
            ls = [x for x in ctx["lows"][start_idx + 1:] if x is not None]
            if ls and min(ls) <= target:
                return None
    if direction == "bullish":
        cross = next((i for i in range(start_idx + 1, n)
                      if closes[i] is not None and closes[i] > trigger), None)
        if cross is None:
            return "forming"
        if last < trigger:
            return "retest"
        if (n - 1) - cross > cfg["break_recent"]:
            return None
        if last > trigger * (1 + cfg["max_ext"]):
            return None
        return "breakout"
    cross = next((i for i in range(start_idx + 1, n)
                  if closes[i] is not None and closes[i] < trigger), None)
    if cross is None:
        return "forming"
    if last > trigger:
        return "retest"
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
    plows = _plows_live(ctx)
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
    # Shoulders must rhyme in TIME as well as price. Searching the whole
    # window for the extreme pivot actively selected DISTANT shoulders
    # (EOG weekly: left shoulder 721 days before the head, right 49 days
    # after — a geometry match, not a pattern; 54% of the book failed
    # 3:1). Search only within 3x the right shoulder's width so patterns
    # re-anchor to their real shoulder instead of the two-year extreme.
    rw = l3_idx - l2_idx
    sym_start = max(win_start, l2_idx - 3 * rw)
    ls_cands = [(i, p) for i, p in plows
                if sym_start <= i <= l2_idx - cfg["min_sep"]]
    if not ls_cands:
        return None
    l1_idx, l1 = min(ls_cands, key=lambda t: t[1])
    if (l2_idx - l1_idx) * 3 < rw:
        return None  # left shoulder hugging the head — asymmetric other way
    if l2 > l1 * (1 - min_hl):
        return None  # head barely below left shoulder — twin lows, that's a
        # double bottom's shape and its detector owns it
    # The neckline is defined by BOTH inter-trough rallies. Using only the
    # head->right-shoulder rally let a single blowoff own the line (ZS
    # weekly: the late-May spike put the "neckline" at $191 while every
    # other rally in the structure stalled at ~$155 — a trigger 35% above
    # price is a spike artifact, not a level). The LOWER rally is the
    # honest line: price has actually contested it from both sides. This
    # also demands a REAL rally between left shoulder and head — without
    # one, the "left shoulder" is just a step in the decline.
    peak_l = _robust_extreme(ctx["highs"][l1_idx:l2_idx + 1], "high")
    peak_r = _robust_extreme(ctx["highs"][l2_idx:l3_idx + 1], "high")
    if not peak_l or not peak_r:
        return None
    neck = min(peak_l, peak_r)
    if neck <= 0 or l3 >= neck * 0.995 or l1 >= neck:
        return None
    depth = (neck - l2) / l2
    if depth < min_depth:
        return None
    # Shoulders must also rhyme in AMPLITUDE: each shoulder needs to stand
    # a real fraction of the head's depth below the neckline. GILD's weekly
    # "left shoulder" sat 1% off the line while the head sat 23% — that's a
    # pause in an ascent, not a shoulder.
    head_amp = neck - l2
    if (neck - l1) < 0.25 * head_amp or (neck - l3) < 0.25 * head_amp:
        return None
    pre = [x for x in ctx["highs"][max(0, l1_idx - (l3_idx - l1_idx)):l1_idx] if x is not None]
    if not pre or max(pre) < neck * (1 + 0.03 * s):
        return None  # didn't come DOWN into this — basing noise, not a reversal
    if ctx["last"] <= l3:
        return None  # higher low already violated
    status = _status(ctx, l3_idx, neck, "bullish", target=neck + (neck - l2))
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
    # Mirror of the iHS shoulder-symmetry rule: search for the left
    # shoulder only within 3x the right shoulder's width from the head.
    rw = h3_idx - h2_idx
    sym_start = max(win_start, h2_idx - 3 * rw)
    ls_cands = [(i, p) for i, p in phighs
                if sym_start <= i <= h2_idx - cfg["min_sep"]]
    if not ls_cands:
        return None
    h1_idx, h1 = max(ls_cands, key=lambda t: t[1])
    if (h2_idx - h1_idx) * 3 < rw:
        return None  # left shoulder hugging the head — asymmetric other way
    if h2 < h1 * (1 + min_lh):
        return None  # head barely above left shoulder — twin highs, that's a
        # double top's shape and its detector owns it
    # Mirror of the iHS neckline rule: the HIGHER of the two inter-peak
    # valleys — a level price defended twice, not a one-off flush.
    val_l = _robust_extreme(ctx["lows"][h1_idx:h2_idx + 1], "low")
    val_r = _robust_extreme(ctx["lows"][h2_idx:h3_idx + 1], "low")
    if not val_l or not val_r:
        return None
    neck = max(val_l, val_r)
    if neck <= 0 or h3 <= neck * 1.005 or h1 <= neck:
        return None
    depth = (h2 - neck) / h2
    if depth < min_depth:
        return None
    # Amplitude mirror of the iHS rule: each shoulder must stand a real
    # fraction of the head's height above the neckline.
    head_amp = h2 - neck
    if (h1 - neck) < 0.25 * head_amp or (h3 - neck) < 0.25 * head_amp:
        return None
    pre = [x for x in ctx["lows"][max(0, h1_idx - (h3_idx - h1_idx)):h1_idx] if x is not None]
    if not pre or min(pre) > neck * (1 - 0.03 * s):
        return None  # didn't come UP into this
    if ctx["last"] >= h3:
        return None  # lower high already violated
    status = _status(ctx, h3_idx, neck, "bearish", target=neck - (h2 - neck))
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
    plows = _plows_live(ctx)
    if len(plows) < 2:
        return None
    # Freshness comes from the LATEST pivot low: a higher low printed after
    # the W is strength, not staleness, so it keeps the pattern alive — the
    # twin pair itself may sit further back (bounded below). A nearer pair
    # that fails downstream gates (undercut, spent, extended) must not
    # shadow an older valid one, so candidate pairs are evaluated in order.
    if (n - 1) - plows[-1][0] > cfg["recent"]:
        return None
    pairs = []
    for j in range(len(plows) - 1, 0, -1):
        j_idx, j_low = plows[j]
        if (n - 1) - j_idx > cfg["max_width"] // 2:
            break  # pair too old to still call live
        win_start = max(0, j_idx - cfg["max_width"])
        cands = [(i, p) for i, p in plows[:j]
                 if win_start <= i <= j_idx - cfg["min_sep"] * 2
                 and abs(j_low - p) / p <= tol]
        for i, p in reversed(cands):  # nearest twin first
            lows_span = [x for x in ctx["lows"][i:] if x is not None]
            if lows_span and min(lows_span) >= min(p, j_low) * 0.999:
                pairs.append((i, p, j_idx, j_low))
    for l1_idx, l1, l2_idx, l2 in pairs[:8]:
        bottom = min(l1, l2)
        trigger = _robust_extreme(ctx["highs"][l1_idx:l2_idx + 1], "high")
        if not trigger:
            continue
        depth = (trigger - bottom) / bottom
        if depth < min_depth:
            continue
        pre = [x for x in ctx["highs"][max(0, l1_idx - (l2_idx - l1_idx)):l1_idx] if x is not None]
        if not pre or max(pre) < trigger * (1 + 0.04 * s):
            continue  # needs a real decline INTO the lows, not sideways chop
        # The second low must have BOUNCED (≥25% of pattern height) — until
        # it does you can't call it a double bottom, and this also stops
        # flat-top triangles / plain chop from masquerading as one.
        if ctx["last"] < bottom + 0.25 * (trigger - bottom):
            continue
        status = _status(ctx, l2_idx, trigger, "bullish", target=trigger + (trigger - bottom))
        if status is None:
            continue
        closeness = abs(l2 - l1) / l1
        quality = min(12.0, 6.0 * depth / min_depth) + max(0.0, 8.0 * (1 - closeness / tol)) \
            + (5.0 if l2_idx - l1_idx >= cfg["min_sep"] * 3 else 2.0)
        points = {"low1": _pt(ctx, l1_idx, l1), "low2": _pt(ctx, l2_idx, l2),
                  "depth_pct": round(depth * 100, 2), "_anchor_price": bottom}
        return _mk(ctx, "double_bottom", "bullish", status, trigger,
                   trigger + (trigger - bottom), bottom, l2_idx, l1_idx, points, quality)
    return None


def _det_double_top(ctx):
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    tol, min_depth = 0.020 * s, 0.06 * s
    phighs = ctx["phighs"]
    if len(phighs) < 2:
        return None
    # Mirror of the double bottom: a lower high after the M keeps the
    # pattern alive; the twin pair may sit further back (bounded below),
    # and nearer pairs that fail downstream gates don't shadow older ones.
    if (n - 1) - phighs[-1][0] > cfg["recent"]:
        return None
    pairs = []
    for j in range(len(phighs) - 1, 0, -1):
        j_idx, j_high = phighs[j]
        if (n - 1) - j_idx > cfg["max_width"] // 2:
            break  # pair too old to still call live
        win_start = max(0, j_idx - cfg["max_width"])
        cands = [(i, p) for i, p in phighs[:j]
                 if win_start <= i <= j_idx - cfg["min_sep"] * 2
                 and abs(j_high - p) / p <= tol]
        for i, p in reversed(cands):  # nearest twin first
            highs_span = [x for x in ctx["highs"][i:] if x is not None]
            if highs_span and max(highs_span) <= max(p, j_high) * 1.001:
                pairs.append((i, p, j_idx, j_high))
    for h1_idx, h1, h2_idx, h2 in pairs[:8]:
        top = max(h1, h2)
        trigger = _robust_extreme(ctx["lows"][h1_idx:h2_idx + 1], "low")
        if not trigger or trigger <= 0:
            continue
        depth = (top - trigger) / top
        if depth < min_depth:
            continue
        pre = [x for x in ctx["lows"][max(0, h1_idx - (h2_idx - h1_idx)):h1_idx] if x is not None]
        if not pre or min(pre) > trigger * (1 - 0.04 * s):
            continue  # needs a real rally INTO the highs, not sideways chop
        # The second high must have been REJECTED (≥25% of pattern height) —
        # otherwise a flat-bottom triangle or chop reads as a double top.
        if ctx["last"] > top - 0.25 * (top - trigger):
            continue
        status = _status(ctx, h2_idx, trigger, "bearish", target=trigger - (top - trigger))
        if status is None:
            continue
        closeness = abs(h2 - h1) / h1
        quality = min(12.0, 6.0 * depth / min_depth) + max(0.0, 8.0 * (1 - closeness / tol)) \
            + (5.0 if h2_idx - h1_idx >= cfg["min_sep"] * 3 else 2.0)
        points = {"high1": _pt(ctx, h1_idx, h1), "high2": _pt(ctx, h2_idx, h2),
                  "depth_pct": round(depth * 100, 2), "_anchor_price": top}
        return _mk(ctx, "double_top", "bearish", status, trigger,
                   trigger - (top - trigger), top, h2_idx, h1_idx, points, quality)
    return None


# ── Reversals: structure shift (higher low / lower high) ─────────────────────
# The playbook's earliest legitimate reversal evidence: after a real decline,
# a swing low, a bounce, and a pullback that HOLDS ABOVE the prior low.
# Trigger = the interim swing extreme (breaking it is the market-structure
# break); invalid = the higher low / lower high itself. Deliberately the
# complement of the double bottom/top: twins within tol are a double, a
# second low sitting ≥ rise_min ABOVE the first is a higher low — the same
# pair can never fire both detectors.

def _det_higher_low(ctx):
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    rise_min, min_depth = 0.020 * s, 0.05 * s
    plows = _plows_live(ctx)
    if len(plows) < 2:
        return None
    l2_idx, l2 = plows[-1]
    if (n - 1) - l2_idx > cfg["recent"]:
        return None
    # Nothing after L2 may undercut it — invalid IS L2, so an undercut means
    # the pattern is already dead, not forming.
    post = [x for x in ctx["lows"][l2_idx:] if x is not None]
    if post and min(post) < l2 * 0.999:
        return None
    # L1: the most recent earlier pivot low that L2 sits meaningfully above.
    # L1 must be THE low — an undercut since means the downtrend resumed,
    # not shifted.
    for l1_idx, l1 in reversed(plows[:-1]):
        if l2_idx - l1_idx > cfg["max_width"] // 2:
            break
        if l2_idx - l1_idx < cfg["min_sep"] * 2:
            continue
        if l2 < l1 * (1 + rise_min):
            continue
        lows_span = [x for x in ctx["lows"][l1_idx:] if x is not None]
        if lows_span and min(lows_span) < l1 * 0.999:
            continue
        trigger = _robust_extreme(ctx["highs"][l1_idx:l2_idx + 1], "high")
        if not trigger or trigger <= l2:
            continue
        depth = (trigger - l1) / l1
        if depth < min_depth:
            continue
        # The pullback must be a real retracement of the upswing — 30-90%.
        # Shallower is a flag's business; a full giveback is the double
        # bottom's (its twin tolerance is excluded by rise_min above).
        retrace = (trigger - l2) / (trigger - l1)
        if not (0.30 <= retrace <= 0.90):
            continue
        # Needs a genuine decline INTO L1 — same gate as the double bottom.
        pre = [x for x in ctx["highs"][max(0, l1_idx - cfg["max_width"] // 2):l1_idx]
               if x is not None]
        if not pre or max(pre) < trigger * (1 + 0.04 * s):
            continue
        # L2 must have bounced (≥25% of its own downswing) before it can be
        # called a higher LOW rather than a pause in a slide.
        if ctx["last"] < l2 + 0.25 * (trigger - l2):
            continue
        status = _status(ctx, l2_idx, trigger, "bullish",
                         target=trigger + (trigger - l1))
        if status is None:
            continue
        quality = min(10.0, 5.0 * depth / min_depth) \
            + max(0.0, 8.0 * (1 - abs(retrace - 0.55) / 0.45)) \
            + (7.0 if (l2 - l1) / l1 >= 2 * rise_min else 3.0)
        points = {"low1": _pt(ctx, l1_idx, l1), "low2": _pt(ctx, l2_idx, l2),
                  "retrace_pct": round(retrace * 100, 1), "_anchor_price": l1}
        return _mk(ctx, "higher_low", "bullish", status, trigger,
                   trigger + (trigger - l1), l2, l2_idx, l1_idx, points, quality)
    return None


def _det_lower_high(ctx):
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    rise_min, min_depth = 0.020 * s, 0.05 * s
    phighs = ctx["phighs"]
    if len(phighs) < 2:
        return None
    h2_idx, h2 = phighs[-1]
    if (n - 1) - h2_idx > cfg["recent"]:
        return None
    post = [x for x in ctx["highs"][h2_idx:] if x is not None]
    if post and max(post) > h2 * 1.001:
        return None
    for h1_idx, h1 in reversed(phighs[:-1]):
        if h2_idx - h1_idx > cfg["max_width"] // 2:
            break
        if h2_idx - h1_idx < cfg["min_sep"] * 2:
            continue
        if h2 > h1 * (1 - rise_min):
            continue
        highs_span = [x for x in ctx["highs"][h1_idx:] if x is not None]
        if highs_span and max(highs_span) > h1 * 1.001:
            continue
        trigger = _robust_extreme(ctx["lows"][h1_idx:h2_idx + 1], "low")
        if not trigger or trigger <= 0 or trigger >= h2:
            continue
        depth = (h1 - trigger) / h1
        if depth < min_depth:
            continue
        retrace = (h2 - trigger) / (h1 - trigger)
        if not (0.30 <= retrace <= 0.90):
            continue
        # Needs a genuine rally INTO H1 — mirror of the higher low's gate.
        pre = [x for x in ctx["lows"][max(0, h1_idx - cfg["max_width"] // 2):h1_idx]
               if x is not None]
        if not pre or min(pre) > trigger * (1 - 0.04 * s):
            continue
        # H2 must have been rejected (≥25% of its own upswing) before it can
        # be called a lower HIGH rather than a pause in a climb.
        if ctx["last"] > h2 - 0.25 * (h2 - trigger):
            continue
        status = _status(ctx, h2_idx, trigger, "bearish",
                         target=trigger - (h1 - trigger))
        if status is None:
            continue
        quality = min(10.0, 5.0 * depth / min_depth) \
            + max(0.0, 8.0 * (1 - abs(retrace - 0.55) / 0.45)) \
            + (7.0 if (h1 - h2) / h1 >= 2 * rise_min else 3.0)
        points = {"high1": _pt(ctx, h1_idx, h1), "high2": _pt(ctx, h2_idx, h2),
                  "retrace_pct": round(retrace * 100, 1), "_anchor_price": h1}
        return _mk(ctx, "lower_high", "bearish", status, trigger,
                   trigger - (h1 - trigger), h2, h2_idx, h1_idx, points, quality)
    return None


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
    status = _status(ctx, lows_in[-1][0], r, "bullish", target=r + (r - first_low))
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
    status = _status(ctx, highs_in[-1][0], sup, "bearish", target=sup - (first_high - sup))
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
    # A falling wedge hangs from its TOP: anchor at the window's highest
    # pivot high and discard everything before it. Requiring the WHOLE
    # window to be wedge-shaped made GILD's textbook Feb-Jul weekly wedge
    # invisible — the window reached back into the pre-top base and the
    # base's pivots poisoned every monotonicity and convergence check.
    top = max(range(len(phs)), key=lambda j: phs[j][1])
    phs = phs[top:]
    if len(phs) < 3:
        return None
    # The wedge's lows live INSIDE it — a stray pivot low before the top
    # would poison the range pairing.
    pls = [x for x in pls if x[0] > phs[0][0]]
    if len(pls) < 2:
        return None
    if not all(phs[j + 1][1] <= phs[j][1] * (1 - step_min) for j in range(len(phs) - 1)):
        return None
    if not all(pls[j + 1][1] <= pls[j][1] for j in range(len(pls) - 1)):
        return None
    first_range = phs[0][1] - pls[0][1]
    last_range = phs[-1][1] - pls[-1][1]
    if first_range <= 0 or last_range <= 0 or last_range > first_range * 0.75:
        return None  # not converging (0.75: range pairing uses the first
        # pivot low AFTER the top, which understates the opening range —
        # GILD's clean wedge measured 0.69 under that pairing)
    if phs[-1][1] > phs[0][1] * (1 - decline_min):
        return None  # not enough of a decline to reverse
    if (n - 1) - phs[-1][0] > cfg["recent"] and (n - 1) - pls[-1][0] > cfg["recent"]:
        return None
    trigger = phs[-1][1]
    if ctx["last"] <= pls[-1][1]:
        return None
    status = _status(ctx, phs[-1][0], trigger, "bullish", target=phs[0][1])
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
    # A rising wedge stands on its BOTTOM: anchor at the window's lowest
    # pivot low, discard everything before it (mirror of the falling
    # wedge's top-anchor fix).
    bot = min(range(len(pls)), key=lambda j: pls[j][1])
    pls = pls[bot:]
    if len(pls) < 3:
        return None
    # Highs must sit INSIDE the structure.
    phs = [x for x in phs if x[0] > pls[0][0]]
    if len(phs) < 2:
        return None
    if not all(pls[j + 1][1] >= pls[j][1] * (1 + step_min) for j in range(len(pls) - 1)):
        return None
    if not all(phs[j + 1][1] >= phs[j][1] for j in range(len(phs) - 1)):
        return None
    first_range = phs[0][1] - pls[0][1]
    last_range = phs[-1][1] - pls[-1][1]
    if first_range <= 0 or last_range <= 0 or last_range > first_range * 0.75:
        return None
    if pls[0][1] <= 0 or pls[-1][1] < pls[0][1] * (1 + climb_min):
        return None
    if (n - 1) - phs[-1][0] > cfg["recent"] and (n - 1) - pls[-1][0] > cfg["recent"]:
        return None
    trigger = pls[-1][1]
    if ctx["last"] >= phs[-1][1]:
        return None
    status = _status(ctx, pls[-1][0], trigger, "bearish", target=pls[0][1])
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

def _det_diamond(ctx, direction: str):
    """Diamond: swings EXPAND into the widest point (broadening half —
    higher highs AND lower lows), then CONTRACT out of it (lower highs AND
    higher lows). Two pivot regimes in sequence — the last classical
    pattern to join the engine and the strictest-gated one, because a
    sloppy diamond detector calls every messy consolidation a diamond.
    Gates: the bulge (apex high + valley low) must sit in the MIDDLE band
    of the span (an edge apex is a triangle/top — their detectors own
    those); both halves must show NET expansion/contraction with pivots
    ordered within noise tolerance; real height; real trend INTO it; v10
    time symmetry between halves. Trigger = the last contracting pivot on
    the breakout side (spike-resistant, engine convention); measured move
    = full pattern height projected from the trigger. QQQ's Jul-2026
    daily diamond top is the reference chart."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    bearish = direction == "bearish"
    min_h = 0.05 * s          # pattern height, fraction of price
    min_exp = 0.01 * s        # net expansion/contraction per side
    tol = 0.005               # pivot-ordering noise tolerance
    # Wedge lesson (v11): a fixed lookback reaches into whatever came
    # before the pattern and poisons the middle-band and monotonicity
    # checks. Try nested spans, widest first — the first clean diamond
    # wins.
    for span in (cfg["max_width"], cfg["max_width"] // 2,
                 cfg["max_width"] // 3, 40):
        r = _diamond_in_window(ctx, direction, span, min_h, min_exp, tol)
        if r:
            return r
    return None


def _diamond_in_window(ctx, direction, span, min_h, min_exp, tol):
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    bearish = direction == "bearish"
    win_start = max(cfg["pivot_k"], (n - 1) - span)
    H = [(i, p) for i, p in ctx["phighs"] if i >= win_start]
    L = [(i, p) for i, p in ctx["plows"] if i >= win_start]
    if len(H) < 3 or len(L) < 3:
        return None
    a_idx, a_val = max(H, key=lambda t: t[1])      # apex high
    v_idx, v_val = min(L, key=lambda t: t[1])      # valley low
    hgt = a_val - v_val
    if v_val <= 0 or hgt / ctx["last"] < min_h:
        return None
    start = min(H[0][0], L[0][0])
    last_piv = max(H[-1][0], L[-1][0])
    width = last_piv - start
    if width < 20:
        return None
    # The bulge lives in the middle: both extremes inside the 25-75% band
    # of the span, and near each other (one bulge, not two humps).
    for mid in (a_idx, v_idx):
        pos = (mid - start) / width
        if not (0.25 <= pos <= 0.75):
            return None
    if abs(a_idx - v_idx) > width * 0.45:
        return None
    # Broadening left half: net expansion on BOTH sides, pivots ordered
    # within tolerance (real charts breathe; strict monotonicity is for
    # textbooks).
    lh = [p for i, p in H if i <= a_idx]
    ll = [p for i, p in L if i <= v_idx]
    if len(lh) < 2 or len(ll) < 2:
        return None
    if lh[0] > a_val * (1 - min_exp) or ll[0] < v_val * (1 + min_exp):
        return None                                   # no real expansion
    if any(lh[j + 1] < lh[j] * (1 - tol) for j in range(len(lh) - 1)):
        return None
    if any(ll[j + 1] > ll[j] * (1 + tol) for j in range(len(ll) - 1)):
        return None
    # Contracting right half: mirror — lower highs and higher lows out of
    # the bulge, with net contraction on both sides.
    rh = [(i, p) for i, p in H if i >= a_idx]
    rl = [(i, p) for i, p in L if i >= v_idx]
    if len(rh) < 2 or len(rl) < 2:
        return None
    if rh[-1][1] > a_val * (1 - min_exp) or rl[-1][1] < v_val * (1 + min_exp):
        return None                                   # no real contraction
    if any(rh[j + 1][1] > rh[j][1] * (1 + tol) for j in range(len(rh) - 1)):
        return None
    if any(rl[j + 1][1] < rl[j][1] * (1 - tol) for j in range(len(rl) - 1)):
        return None
    # v10 time symmetry between the halves.
    lw = max(a_idx, v_idx) - start
    rw = last_piv - min(a_idx, v_idx)
    if lw <= 0 or rw <= 0 or not (1 / 3 <= rw / lw <= 3):
        return None
    if (n - 1) - last_piv > cfg["recent"]:
        return None                                   # gone stale
    # Real trend INTO the pattern: a diamond TOP caps a rally (the run-in
    # must start well below the valley); a bottom caps a decline.
    pre = [x for x in (ctx["lows"] if bearish else ctx["highs"])
           [max(0, start - width):start] if x is not None]
    if not pre:
        return None
    if bearish and min(pre) > v_val * (1 - 0.04 * s):
        return None
    if not bearish and max(pre) < a_val * (1 + 0.04 * s):
        return None
    if bearish:
        trigger = rl[-1][1]                # contracting support
        target = trigger - hgt
        invalid = rh[-1][1]                # the last lower high overhead
        anchor_idx, anchor_val = a_idx, a_val
    else:
        trigger = rh[-1][1]                # contracting resistance
        target = trigger + hgt
        invalid = rl[-1][1]
        anchor_idx, anchor_val = v_idx, v_val
    if trigger <= 0 or not (v_val < trigger < a_val):
        return None
    status = _status(ctx, last_piv, trigger, direction, target=target)
    if status is None:
        return None
    quality = min(8.0, 4.0 * (hgt / ctx["last"]) / min_h) \
        + min(8.0, 2.0 * (len(H) + len(L) - 6)) \
        + (6.0 if 0.6 <= rw / lw <= 1.67 else 2.0)
    points = {"apex": _pt(ctx, a_idx, a_val), "valley": _pt(ctx, v_idx, v_val),
              "width_bars": width, "height_pct": round(hgt / ctx["last"] * 100, 2),
              "_anchor_price": anchor_val}
    return _mk(ctx, "diamond_top" if bearish else "diamond_bottom",
               direction, status, trigger, target, invalid,
               anchor_idx, start, points, quality)


def _det_diamond_top(ctx):
    return _det_diamond(ctx, "bearish")


def _det_diamond_bottom(ctx):
    return _det_diamond(ctx, "bullish")


def _det_cup_handle(ctx):
    """Rounded base between two rims at the same level, then a shallow handle
    pause under the rim — breakout over the rim completes it."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    # Rim tolerance 2.5%·scale: at 4% the detector paired MRK's Feb $125.14
    # high with the July $130.29 high — a tilted "cup" whose left rim was
    # really an inverse-H&S neckline touch. Rims must actually be level.
    rim_tol, min_depth, max_depth = 0.025 * s, 0.10 * s, 0.55
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
    status = _status(ctx, r_idx, rim, "bullish", target=rim + (rim - bottom))
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


# ── EMA 8/13 pullback bounce (continuation) ──────────────────────────────────

def _ema_seq(vals: list, span: int) -> list:
    """EMA over a list (None forward-filled); same length as input."""
    k = 2.0 / (span + 1)
    out, prev, e = [], None, None
    for v in vals:
        if v is None:
            v = prev
        if v is None:
            out.append(None)
            continue
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
        prev = v
    return out



def _impulse_floor(ctx, start: int, end: int) -> float:
    """Volatility-normalized impulse requirement for the EMA bounce/reject:
    a real leg is ~4x the name's own average daily true range — a 6% move
    on a stock that travels 1.2%/day is a monster; a 10% pop on a 4%/day
    biotech can be noise. Noise is measured over the bars BEFORE the run
    (the impulse's own candles would inflate it), falling back to the run
    window when history is short. The old fixed 10%-scale bar remains as a
    CAP (anything that big qualifies regardless of volatility) and a
    3.5%-scale floor keeps ultra-quiet names from firing on nothing."""
    s = ctx["cfg"]["scale"]
    highs, lows, closes = ctx["highs"], ctx["lows"], ctx["closes"]

    def _atrp(a: int, b: int):
        trs = []
        for i in range(max(a, 1), b):
            h, lo, c, pc = highs[i], lows[i], closes[i], closes[i - 1]
            if None in (h, lo, c, pc) or c <= 0:
                continue
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)) / c)
        return sum(trs) / len(trs) if trs else None

    width = max(end - start, 5)
    atrp = _atrp(start - width, start) or _atrp(start, end)
    if atrp is None:
        return 0.10 * s
    return max(0.035 * s, min(0.10 * s, 4.0 * atrp))


def _det_ema_bounce(ctx):
    """Eric's continuation setup: a strong impulse with the fast EMAs
    stacked (8 over 13 over 21), then a SHALLOW 2-7 candle pullback that
    lands on the 8/13 EMA zone and holds — the trend's first-touch rebate.
    Trigger = reclaiming the 8 EMA off the pullback low; invalid = that
    low; target = the impulse repeated from the trigger."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    closes, lows = ctx["closes"], ctx["lows"]
    if n < 40 or any(c is None for c in closes[-30:]):
        return None
    e8 = _ema_seq(closes, 8)
    e13 = _ema_seq(closes, 13)
    e21 = _ema_seq(closes, 21)
    if e21[-1] is None:
        return None
    # Peak of the impulse: highest close of the recent window, with a
    # 2-7 bar pullback after it ("a few candles").
    seg = closes[-11:]
    peak_idx = (n - len(seg)) + max(range(len(seg)), key=lambda j: seg[j])
    pull_len = (n - 1) - peak_idx
    if not (2 <= pull_len <= 7):
        return None
    peak = closes[peak_idx]
    run_start = max(0, peak_idx - cfg["run_len"])
    base_vals = [c for c in closes[run_start:peak_idx] if c is not None]
    if not base_vals:
        return None
    base = min(base_vals)
    if base <= 0:
        return None
    thr = _impulse_floor(ctx, run_start, peak_idx)
    if peak / base - 1 < thr:
        return None                                  # no real impulse (vol-adjusted)
    if not (e8[peak_idx] and e8[peak_idx] > e13[peak_idx] > e21[peak_idx]):
        return None                                  # EMAs not stacked = no trend
    # Pullback must TOUCH the 8/13 zone, hold the 21 on a closing basis,
    # and keep most of the impulse.
    touched = any(lows[i] is not None and e13[i] is not None
                  and lows[i] <= e13[i] * (1 + 0.002 * s)
                  for i in range(peak_idx + 1, n))
    if not touched:
        return None
    if any(closes[i] is not None and e21[i] is not None
           and closes[i] < e21[i] * (1 - 0.01 * s)
           for i in range(peak_idx + 1, n)):
        return None                                  # closed through the 21 — broken
    pull_pairs = [(i, lows[i]) for i in range(peak_idx + 1, n)
                  if lows[i] is not None]
    low_idx, pull_low = min(pull_pairs, key=lambda t: t[1])
    if pull_low < peak - 0.62 * (peak - base):
        return None                                  # gave back too much
    trigger = e8[-1]
    target = trigger + (peak - base)
    # Cross detection starts at the pullback LOW — the bounce is what
    # crosses the trigger, not the fade into the zone.
    status = _status(ctx, low_idx, trigger, "bullish", target=target)
    if status is None:
        return None
    held_8 = pull_low >= e8[-1] * (1 - 0.005 * s)
    quality = min(10.0, 5.0 * ((peak / base - 1) / max(thr, 1e-9))) \
        + (8.0 if held_8 else 4.0) + max(0.0, 7.0 - pull_len)
    points = {"impulse_low": round(base, 4), "peak": _pt(ctx, peak_idx, peak),
              "pullback_low": round(pull_low, 4), "ema8": round(e8[-1], 4),
              "ema13": round(e13[-1], 4), "pull_bars": pull_len,
              "_anchor_price": pull_low}
    return _mk(ctx, "ema_bounce", "bullish", status, trigger, target,
               pull_low, low_idx, run_start, points, quality)


def _det_ema_reject(ctx):
    """Bearish mirror: a strong impulse DOWN with the fast EMAs stacked
    below, then a weak 2-7 candle rally into the falling 8/13 zone that
    stalls — short the rejection."""
    cfg, n, s = ctx["cfg"], ctx["n"], ctx["cfg"]["scale"]
    closes, highs = ctx["closes"], ctx["highs"]
    if n < 40 or any(c is None for c in closes[-30:]):
        return None
    e8 = _ema_seq(closes, 8)
    e13 = _ema_seq(closes, 13)
    e21 = _ema_seq(closes, 21)
    if e21[-1] is None:
        return None
    seg = closes[-11:]
    trough_idx = (n - len(seg)) + min(range(len(seg)), key=lambda j: seg[j])
    rally_len = (n - 1) - trough_idx
    if not (2 <= rally_len <= 7):
        return None
    trough = closes[trough_idx]
    run_start = max(0, trough_idx - cfg["run_len"])
    base_vals = [c for c in closes[run_start:trough_idx] if c is not None]
    if not base_vals:
        return None
    base = max(base_vals)
    if trough <= 0:
        return None
    thr = _impulse_floor(ctx, run_start, trough_idx)
    if base / trough - 1 < thr:
        return None
    if not (e8[trough_idx] and e8[trough_idx] < e13[trough_idx] < e21[trough_idx]):
        return None
    touched = any(highs[i] is not None and e13[i] is not None
                  and highs[i] >= e13[i] * (1 - 0.002 * s)
                  for i in range(trough_idx + 1, n))
    if not touched:
        return None
    if any(closes[i] is not None and e21[i] is not None
           and closes[i] > e21[i] * (1 + 0.01 * s)
           for i in range(trough_idx + 1, n)):
        return None
    rally_pairs = [(i, highs[i]) for i in range(trough_idx + 1, n)
                   if highs[i] is not None]
    high_idx, rally_high = max(rally_pairs, key=lambda t: t[1])
    if rally_high > trough + 0.62 * (base - trough):
        return None
    trigger = e8[-1]
    target = trigger - (base - trough)
    status = _status(ctx, high_idx, trigger, "bearish", target=target)
    if status is None:
        return None
    held_8 = rally_high <= e8[-1] * (1 + 0.005 * s)
    quality = min(10.0, 5.0 * ((base / trough - 1) / max(thr, 1e-9))) \
        + (8.0 if held_8 else 4.0) + max(0.0, 7.0 - rally_len)
    points = {"impulse_high": round(base, 4),
              "trough": _pt(ctx, trough_idx, trough),
              "rally_high": round(rally_high, 4), "ema8": round(e8[-1], 4),
              "ema13": round(e13[-1], 4), "pull_bars": rally_len,
              "_anchor_price": rally_high}
    return _mk(ctx, "ema_reject", "bearish", status, trigger, target,
               rally_high, high_idx, run_start, points, quality)


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


# ── The goat line: 200-week SMA touch (added 2026-08-10) ─────────────────────

def _det_wma_touch(ctx):
    """The goat study, formalized the day it ran (2026-08-10): a long-
    qualified uptrend meeting its 200-WEEK SMA — "keep it simple, buy the
    200." The study's grade on our own 2005+ bars: a 40-week-qualified
    touch of the 200-week line reached +5% before a 3% close-through
    failure 82.2% of the time and +10% 58.2% (n=2,653 events, ~126/yr
    across the universe) — the strongest cut in the whole study. The
    caveats travel with the number: survivorship-flattered (delisted
    names absent), events begin ~2010 (MA warm-up), grades are
    touch-entry grades.

    Scan semantics (weekly bars only, needs 240+ COMPLETED weeks — the
    deep weekly fetch, not the regular ~3y map; NOT in DETECTORS so the
    other detectors' inputs are untouched):
      qualifier — each of the last 40 completed weeks CLOSED above its
        prior-week 200w SMA (the study's established-trend gate);
      trigger   — the 200w SMA over the last 200 completed weeks: the
        line a resting limit parks on;
      invalid   — 3% below the trigger, judged on closes (wick rule);
      target    — +10% from the trigger, the tier whose prior rides in
        points;
      status    — 'breakout' inside the 0-4% approach band (armable: the
        7:40 writer parks the limit AT the line), 'forming' when 4-12%
        above (approaching — visible, not armable), 'retest' at/under
        the line awaiting the close verdict, None once closed through
        the invalid or still >12% away.
    The study's gap-through exclusion is NOT re-implemented here: the
    trigger loop's DOA/reclaim fill model already enforces it at
    execution time — one rule for every book."""
    if ctx["tf"] != "weekly":
        return None
    closes = ctx["closes"]
    if len(closes) < 242 or any(c is None for c in closes[-242:]):
        return None
    completed = closes[:-1]          # the last bar is the live partial week
    n_c = len(completed)
    if n_c < 241:
        return None
    trigger = sum(completed[-200:]) / 200.0
    if trigger <= 0:
        return None
    # Qualifier walk, newest completed week backwards: week j must close
    # above the 200w SMA of the 200 weeks BEFORE it (prior-week line,
    # exactly as the study graded it). Counted to 120 for scoring.
    up_run, j = 0, n_c - 1
    win = sum(completed[j - 200:j])
    while up_run < 120 and j >= 200:
        if completed[j] <= win / 200.0:
            break
        up_run += 1
        j -= 1
        if j >= 200:
            win += completed[j - 200] - completed[j]
    if up_run < 40:
        return None
    # Low-volatility guard (found on the study's own event list: SPMB and
    # friends — bond ETFs hug their 200w line permanently, so a touch is
    # noise and the +10% target is unreachable on pattern timescales). A
    # real uptrend puts distance between itself and its 200-week line;
    # demand the QUALIFIED RUN's own high be 15%+ above it. (First cut
    # used the trailing 240-week high and SPMB slipped through on its
    # 2021 DOWNTREND high — the amplitude must belong to this trend.)
    if max(completed[-min(up_run, 240):]) < trigger * 1.15:
        return None
    invalid = trigger * 0.97
    target = trigger * 1.10
    last = ctx["last"]
    if last < invalid:
        return None                       # closed through the line — dead
    dist = (last - trigger) / trigger * 100.0
    if last <= trigger:
        status = "retest"
    elif dist <= 4.0:
        status = "breakout"
    elif dist <= 12.0:
        status = "forming"
    else:
        return None                       # trend intact, line far — not listable
    anchor_idx = min(j + 1, n_c - 1)      # first week of the counted run
    # Passing the 40-week gate IS the pattern — it earns base credit, so a
    # qualified touch clears the writer's 70 bar even on a name with only
    # the minimum 241 weeks of history (whose run-count is capped by data
    # depth, not by trend quality).
    quality = 12.0 + min(8.0, (up_run - 40) / 10.0) + (5.0 if dist <= 8.0 else 2.0)
    points = {"wma200": round(trigger, 4), "up_weeks": up_run,
              "prior": "goat study 2026-08-10: 82% to +5%, 58% to +10% "
                       "(n=2,653; survivorship-flattered)",
              "_anchor_price": trigger}
    return _mk(ctx, "wma_touch", "bullish", status, trigger, target,
               invalid, anchor_idx, anchor_idx, points, quality)


DETECTORS = [_det_inverse_hs, _det_hs_top, _det_double_bottom, _det_double_top,
             _det_higher_low, _det_lower_high,
             _det_bull_flag, _det_bear_flag, _det_asc_triangle,
             _det_desc_triangle, _det_falling_wedge, _det_rising_wedge,
             _det_diamond_top, _det_diamond_bottom,
             _det_cup_handle, _det_range_break,
             _det_ema_bounce, _det_ema_reject]


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
            UNION
            -- Index/sector/theme ETFs: tradeable in their own right, and an
            -- index-level pattern (QQQ diamond, IWM base) is where-the-
            -- market-leans information the stock book can't provide.
            SELECT ticker FROM etf_theme_map
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
    """~1y of daily OHLC bars per ticker, oldest → newest. high/low fall back
    to close for rows not yet OHLC-backfilled (migration 0062), so the series
    is a clean mix of wick bars and close-only bars."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, trade_date,
                   COALESCE(high, close), COALESCE(low, close), close, volume
            FROM daily_prices
            WHERE ticker = ANY(%s) AND trade_date >= CURRENT_DATE - 400
            ORDER BY ticker, trade_date
        """, (tickers,))
        return _rows_to_bars(cur.fetchall())


def _fetch_weekly(conn, tickers: list) -> dict:
    """~3y of weekly OHLC bars aggregated in SQL (true wick high/low when
    present, else close), oldest → newest. The current (partial) week rides
    along as the live bar."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, date_trunc('week', trade_date)::date AS wk,
                   max(COALESCE(high, close)), min(COALESCE(low, close)),
                   (array_agg(close ORDER BY trade_date DESC))[1],
                   sum(volume)
            FROM daily_prices
            WHERE ticker = ANY(%s) AND trade_date >= CURRENT_DATE - 1120
            GROUP BY ticker, date_trunc('week', trade_date)
            ORDER BY ticker, wk
        """, (tickers,))
        return _rows_to_bars(cur.fetchall())


def _fetch_weekly_deep(conn, tickers: list) -> dict:
    """~4.9y of weekly bars for the 200-week detector ONLY (240 completed
    weeks + buffer). A separate query, not a widening of _fetch_weekly:
    the other weekly detectors compute pivots over their whole input, so
    feeding them five years instead of three would silently change their
    detections. Isolation costs one extra aggregate per batch; the 6:45
    scan owns its own statement timeout and retry armor."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, date_trunc('week', trade_date)::date AS wk,
                   max(COALESCE(high, close)), min(COALESCE(low, close)),
                   (array_agg(close ORDER BY trade_date DESC))[1],
                   sum(volume)
            FROM daily_prices
            WHERE ticker = ANY(%s) AND trade_date >= CURRENT_DATE - 1780
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
                        %(dist_to_trigger_pct)s, %(score)s, now(), clock_timestamp())
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
                    scanned_at = clock_timestamp()
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
        # The weekly GROUP BY over ~3y of a multi-million-row table is heavy;
        # give this batch job real headroom over the default 120s per-statement
        # cap (a single slow batch used to trip it and abort the whole scan).
        try:
            with conn.cursor() as _c:
                _c.execute("SET statement_timeout = '600s'")
            conn.commit()
        except Exception:
            pass
        # Cutoff for the stale-row sweep MUST come from the DB clock, not the
        # app clock: rows are stamped scanned_at = clock_timestamp() (DB), and
        # comparing those against an app-captured time is subject to app/DB
        # clock skew — which was silently deleting the daily rows this scan had
        # just inserted while weekly (a later transaction) survived.
        with conn.cursor() as _c:
            _c.execute("SELECT clock_timestamp()")
            run_started = _c.fetchone()[0]
        tickers = _universe(conn)
        log.info(f"[patterns] daily/weekly scan over {len(tickers)} names")
        rows_daily, rows_weekly = [], []
        # Smaller batches keep each aggregation query well inside the timeout.
        for i in range(0, len(tickers), 120):
            batch = tickers[i:i + 120]
            daily_map = _fetch_daily(conn, batch)
            weekly_map = _fetch_weekly(conn, batch)
            deep_map = _fetch_weekly_deep(conn, batch)
            for t in batch:
                for r in detect_patterns(daily_map.get(t) or [], "daily"):
                    r["ticker"] = t
                    rows_daily.append(r)
                for r in detect_patterns(weekly_map.get(t) or [], "weekly"):
                    r["ticker"] = t
                    rows_weekly.append(r)
                # The goat line runs off its own deep bars, called directly
                # (not via DETECTORS) so the regular detectors' inputs stay
                # byte-for-byte what they were before it existed.
                deep_ctx = _ctx(deep_map.get(t) or [], "weekly")
                if deep_ctx:
                    try:
                        r = _det_wma_touch(deep_ctx)
                        if r:
                            r["timeframe"] = "weekly"
                            r["ticker"] = t
                            rows_weekly.append(r)
                    except Exception as e:
                        log.warning(f"[patterns] wma_touch error on {t}: {e}")
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
    """Priority-ordered: the cap must trim the liquidity tail, never the
    watchlist — sorted()[:cap] was alphabetical and silently dropped every
    T–Z name (XLV included) once the pool outgrew FOUR_H_MAX_CANDIDATES."""
    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM watchlist WHERE active = true "
                    "ORDER BY ticker")
        wl = [r[0] for r in cur.fetchall() if r[0]]
        cur.execute("""
            SELECT ticker FROM daily_prices
            WHERE trade_date >= CURRENT_DATE - 30
            GROUP BY ticker
            ORDER BY avg(close * volume) DESC NULLS LAST
            LIMIT %s
        """, (FOUR_H_LIQUID_TOP,))
        liquid = [r[0] for r in cur.fetchall() if r[0]]
        cur.execute("SELECT DISTINCT ticker FROM pattern_scan "
                    "WHERE timeframe = '4h' ORDER BY ticker")
        prior = [r[0] for r in cur.fetchall() if r[0]]
    fresh = sorted(set(hits.get("daily") or []) | set(hits.get("weekly") or []))
    out, seen = [], set()
    for bucket in (wl, fresh, liquid, prior):
        for t in bucket:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out[:FOUR_H_MAX_CANDIDATES]


def _bars_fresh(bars: list, latest_session: str | None) -> bool:
    """A 4h feed whose last bar predates the latest completed daily session is
    serving the past — patterns computed on it are fiction (BKNG printed
    last_close 177.26 on a day it traded 201.30; MSFT 415.89 against a 393
    tape for three days). Stale => skip the ticker: the run's prune then drops
    its old rows, and absence beats garbage. No latest_session to compare
    against => treat as fresh rather than blanking the whole timeframe."""
    if not bars:
        return False
    if not latest_session:
        return True
    return str(bars[-1].get("date") or "") >= str(latest_session)


def scan_4h(hits: dict = None) -> int:
    """4-hour scan over watchlist + weekly/daily hits + the most liquid names.
    Session-anchored 4h bars (9:30/13:30 ET, RTH) — the same helper the FVG
    drawer uses since #148; the scanner had kept Polygon's clock-anchored
    multiplier=4 buckets, candles no chart draws. Each candidate's bars are
    freshness-gated before detection."""
    from screen.reversal_screen import _conn
    from analysis.polygon_data import fetch_session_4h_bars

    conn = _conn()
    try:
        with conn.cursor() as _c:
            _c.execute("SELECT clock_timestamp()")   # DB clock — see scan_db_timeframes
            run_started = _c.fetchone()[0]
            _c.execute("SELECT max(trade_date)::text FROM daily_prices")
            latest_session = (_c.fetchone() or [None])[0]
        cands = _four_h_candidates(conn, hits or {})
        log.info(f"[patterns] 4h scan over {len(cands)} candidates "
                 f"(freshness floor: {latest_session})")

        def _one(t):
            bars = fetch_session_4h_bars(t, days=120)
            if not _bars_fresh(bars, latest_session):
                last = bars[-1].get("date") if bars else "no bars"
                log.warning(f"[patterns] 4h {t}: stale feed (last bar {last} vs "
                            f"session {latest_session}) — skipped; no rows written "
                            f"and this run's prune drops its old rows")
                return []
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
                # Diamonds are QUARANTINED from intraday trigger alerts
                # until the honest backtest grades them and the owner has
                # eyeballed the detections — they show on the Patterns tab
                # like everything else, but a new detector doesn't get to
                # page anyone before it earns trust.
                cur.execute("""
                    SELECT ticker, timeframe, pattern, direction,
                           trigger_price, target, score
                    FROM pattern_scan WHERE status IN ('forming', 'retest')
                      AND pattern NOT IN ('diamond_top', 'diamond_bottom')
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
            # Contract suggestion rides in the alert so it arrives ready to
            # act on (empty on any options-data hiccup — never blocks).
            opt = ""
            try:
                from analysis.options_picker import ticket_one_liner
                opt = ticket_one_liner(ticker)
            except Exception:
                pass
            rows.append({
                "ticker": ticker,
                "sleeve": "pattern",
                "signal_type": "PATTERN_BREAKOUT" if bullish else "PATTERN_BREAKDOWN",
                "score": _TF_SCORE.get(pat["timeframe"], 70.0),
                "rationale": ((f"{name} ({tf}): {verb} ${trig:,.2f} — "
                               f"target ${pat['target']:,.2f} ({move:+.1f}%)")[:200]
                              + (f" {opt}" if opt else "")),
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
