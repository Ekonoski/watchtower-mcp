"""
Watchtower Imbalances — fair value gaps and inversions.

A displacement candle can move so fast that candle #1's high and candle
#3's low never overlap: the zone between them traded one way only.
Doctrine (per the FVG study card): a gap is a LEVEL WITH EDGES —
respected it acts as support/resistance; closed through, it INVERTS
(polarity flip) and the retest of the dead zone is the failed-reclaim
entry with a sharper stop (acceptance beyond the far edge).

House rules encoded here:
  displacement-quality only   the middle candle must be a conviction
                              candle — body >= 1.5x the trailing median
  wick vs close               a wick through the zone consumes it
                              (filled); only a CLOSE through inverts it
  freshness                   stale inversions (30+ bars) stop mattering;
                              newest gaps sort first
Timeframe-agnostic: pass any OHLC bar list, oldest first.
"""


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def detect_fvgs(bars: list, max_gaps: int = 8,
                min_body_ratio: float = 1.5) -> list:
    """Open + recently-inverted gaps from OHLC dicts (oldest first).
    Returns [{side, status, top, bottom, mid, age_bars}] newest-first."""
    n = len(bars)
    if n < 10:
        return []
    bodies = [abs((b.get("close") or 0) - (b.get("open") or 0)) for b in bars]
    raw = []
    for i in range(2, n):
        med = _median(bodies[max(0, i - 21):i - 1])
        if not med or bodies[i - 1] < min_body_ratio * med:
            continue    # middle candle isn't a displacement — noise, not imbalance
        if bars[i]["low"] > bars[i - 2]["high"]:
            raw.append({"side": "bullish", "top": bars[i]["low"],
                        "bottom": bars[i - 2]["high"], "born": i - 1})
        if bars[i]["high"] < bars[i - 2]["low"]:
            raw.append({"side": "bearish", "top": bars[i - 2]["low"],
                        "bottom": bars[i]["high"], "born": i - 1})
    out = []
    for g in raw:
        status, inverted_at = "open", None
        for j in range(g["born"] + 2, n):
            b = bars[j]
            if g["side"] == "bullish":
                if b["close"] < g["bottom"]:
                    status, inverted_at = "inverted", j
                    break
                if b["low"] <= g["bottom"]:
                    status = "filled"
                    break
            else:
                if b["close"] > g["top"]:
                    status, inverted_at = "inverted", j
                    break
                if b["high"] >= g["top"]:
                    status = "filled"
                    break
        if status == "filled":
            continue
        if status == "inverted" and (n - 1 - inverted_at) > 30:
            continue
        out.append({
            "side": g["side"], "status": status,
            "top": round(float(g["top"]), 2),
            "bottom": round(float(g["bottom"]), 2),
            "mid": round((float(g["top"]) + float(g["bottom"])) / 2, 2),
            "age_bars": n - 1 - g["born"],
        })
    out.sort(key=lambda g: g["age_bars"])
    return out[:max_gaps]
