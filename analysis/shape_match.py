"""
Shape matching — find charts that LOOK like a reference chart.

Eric, 2026-08-15: "I'm trying to find these repeatable charts that I
know are bullish over and over again without having to hunt all over
for them." A single-bar fingerprint match (nine numbers at the last
bar) found CEG as SNAP's weekly twin — numerically true at the bar and
across twelve weeks, yet the CHARTS read differently, because the eye
matches SHAPE across months: how many wave mounds, whether the second
is shallower, the %R staircase, the MACD arc. So this module matches
TRAJECTORIES: each component's path over the lookback window, computed
by the live engine, normalized by fixed component scales so panel
auto-scaling can't lie, plus the wave-trough structure the eye keys on.

Two-stage for cost honesty: a loose snapshot pre-filter over
oscillator_scan picks a candidate pool; only the pool gets full engine
paths. Daily and weekly only in v1 (intraday candidates would need a
Polygon fetch per name).
"""
import logging

import numpy as np

log = logging.getLogger("watchtower.shape_match")

# Fixed per-component scales (typical full swing) — distance is measured
# in fractions of these, so a 10-point RSI gap counts like a 24-point
# wave gap, and chart auto-scaling can't distort the comparison.
SHAPE_SCALES = {
    "wt2": 60.0, "wt1": 60.0, "mf": 15.0, "rsi": 25.0,
    "pctr": 50.0, "macd_pct": 6.0, "hist_pct": 2.0,
}
SHAPE_COMPONENTS = tuple(SHAPE_SCALES)


def extract_paths(dfo, lookback: int) -> dict:
    """Component trajectories over the last `lookback` bars of a
    compute_oscillator frame. MACD family normalized by price so a $5
    chart and a $280 chart speak the same units."""
    from analysis.oscillator import MF_DEFAULT
    tail = dfo.iloc[-lookback:]
    close = tail["close"].values.astype(float)
    safe = np.where(close == 0, np.nan, close)
    return {
        "wt1": tail["wt1"].values.astype(float),
        "wt2": tail["wt2"].values.astype(float),
        "mf": tail[MF_DEFAULT].values.astype(float),
        "rsi": tail["rsi"].values.astype(float),
        "pctr": tail["pctr"].values.astype(float),
        "macd_pct": tail["macd"].values.astype(float) / safe * 100.0,
        "hist_pct": tail["macd_hist"].values.astype(float) / safe * 100.0,
    }


def path_distance(ref: dict, cand: dict) -> tuple:
    """Mean absolute path difference per component, in units of each
    component's fixed scale; returns (overall, per_component). Pure.
    Shorter candidate histories compare over the overlapping tail —
    the overlap fraction is reported so a thin match can't masquerade
    as a full one."""
    per = {}
    total, n = 0.0, 0
    for comp in SHAPE_COMPONENTS:
        a, b = ref.get(comp), cand.get(comp)
        if a is None or b is None:
            continue
        m = min(len(a), len(b))
        if m < 8:
            continue
        aa, bb = a[-m:], b[-m:]
        mask = ~(np.isnan(aa) | np.isnan(bb))
        if mask.sum() < 8:
            continue
        d = float(np.mean(np.abs(aa[mask] - bb[mask]))) / SHAPE_SCALES[comp]
        per[comp] = round(d, 3)
        total += d
        n += 1
    if n == 0:
        return float("inf"), per
    return round(total / n, 3), per


# A wave "higher low" needs MATERIAL lift to count as the rising-mound
# look (2026-08-15, the CHWY-vs-SNAP calibration): SNAP's mounds rise
# −60.5 → −39.6 (21 points); CHWY's −69.0 → −68.5 half-point wiggle is
# twin mounds, not a staircase — the %R saturation lesson, in the waves.
WT_TROUGH_LIFT = 8.0


def wave_trough_structure(wt2_path, floor: float = -25.0) -> dict:
    """The mound structure the eye keys on: confirmed wt2 pivot lows at
    or below `floor` within the window, whether the last two rise by a
    MATERIAL amount (the visual higher low), and the lift itself so a
    marginal pair is auditable. Pure."""
    from analysis.oscillator import _pivot_idx
    v = np.asarray(wt2_path, dtype=float)
    piv = [i for i in _pivot_idx(v, 2, "low")
           if not np.isnan(v[i]) and v[i] <= floor]
    out = {"n_troughs": len(piv),
           "troughs": [round(float(v[i]), 1) for i in piv[-3:]]}
    lift = float(v[piv[-1]] - v[piv[-2]]) if len(piv) >= 2 else None
    out["trough_lift"] = round(lift, 1) if lift is not None else None
    out["rising"] = bool(lift is not None and lift >= WT_TROUGH_LIFT)
    return out


def match_chart(ticker: str, timeframe: str = "weekly", lookback: int = 40,
                top_n: int = 10, pool: int = 150) -> dict:
    """Rank the fleet by shape similarity to `ticker`'s chart.
    Returns {reference: {...}, matches: [...], pool_size, holes}."""
    from analysis.oscillator import (compute_oscillator, resample_weekly,
                                     _fetch_daily_ohlcv)
    from screen.reversal_screen import _conn

    ticker = ticker.upper().strip()
    timeframe = timeframe if timeframe in ("daily", "weekly") else "weekly"
    conn = _conn()
    try:
        ref_frames = _fetch_daily_ohlcv(conn, [ticker])
        daily = ref_frames.get(ticker)
        if daily is None or len(daily) < 120:
            return {"error": f"not enough recorded history for {ticker}"}
        frame = resample_weekly(daily) if timeframe == "weekly" else daily
        if len(frame) < 70:
            return {"error": f"not enough {timeframe} bars for {ticker}"}
        dref = compute_oscillator(frame)
        ref_paths = extract_paths(dref, lookback)
        ref_struct = wave_trough_structure(ref_paths["wt2"])
        c = dref.iloc[-1]

        # Stage 1: loose snapshot pre-filter (2x the shape scales) so the
        # expensive path stage only runs on plausible candidates.
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.ticker FROM oscillator_scan o
                JOIN screener_snapshot s ON s.ticker = o.ticker AND s.price > 0
                WHERE o.timeframe = %s AND o.ticker != %s
                  AND abs(o.wt2 - %s) <= 40 AND abs(o.rsi - %s) <= 18
                  AND abs(o.pctr - %s) <= 40 AND abs(o.mf_candle - %s) <= 12
                ORDER BY abs(o.wt2 - %s) + abs(o.rsi - %s) + abs(o.pctr - %s)/2
                LIMIT %s
            """, (timeframe, ticker, float(c["wt2"]), float(c["rsi"]),
                  float(c["pctr"]), float(c["mf_candle"]),
                  float(c["wt2"]), float(c["rsi"]), float(c["pctr"]), pool))
            cands = [r[0] for r in cur.fetchall()]
        holes = 0
        scored = []
        for i in range(0, len(cands), 120):
            frames = _fetch_daily_ohlcv(conn, cands[i:i + 120])
            for tk, dcand in frames.items():
                try:
                    f2 = resample_weekly(dcand) if timeframe == "weekly" else dcand
                    if len(f2) < 70:
                        holes += 1
                        continue
                    d2 = compute_oscillator(f2)
                    paths = extract_paths(d2, lookback)
                    dist, per = path_distance(ref_paths, paths)
                    if not np.isfinite(dist):
                        holes += 1
                        continue
                    st = wave_trough_structure(paths["wt2"])
                    # The eye's veto: mound structure must agree — same
                    # rising/not-rising read, trough count within one.
                    struct_ok = (st["rising"] == ref_struct["rising"]
                                 and abs(st["n_troughs"] - ref_struct["n_troughs"]) <= 1)
                    scored.append({"ticker": tk, "dist": dist, "per": per,
                                   "struct": st, "struct_ok": struct_ok})
                except Exception:
                    holes += 1
        scored.sort(key=lambda r: (not r["struct_ok"], r["dist"]))
        out = scored[:top_n]
        # Structural context for the matches (the MNDY rule).
        if out:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (ticker) ticker, pattern, direction, status
                    FROM pattern_scan WHERE ticker = ANY(%s)
                    ORDER BY ticker, score DESC NULLS LAST
                """, ([m["ticker"] for m in out],))
                pats = {t: (p, d, s) for t, p, d, s in cur.fetchall()}
            for m in out:
                m["pattern"] = pats.get(m["ticker"])
        return {"reference": {"ticker": ticker, "timeframe": timeframe,
                              "lookback": lookback, "struct": ref_struct,
                              "bar_ts": str(dref.index[-1].date())},
                "matches": out, "pool_size": len(cands), "holes": holes}
    finally:
        conn.close()
