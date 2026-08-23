"""
The structure screen (2026-08-23, Eric: "Can we find the major levels
automatically and screen for names?").

The desk already owns the hard part: the levels engine clusters pivots
into multi-touch shelves (tests/test_levels_shelf.py pins the IREN
case). This screen runs that engine FLEET-WIDE off recorded daily
bars — no vendor calls — and flags the tradeable lifecycle at MAJOR
levels only (>=3 touches):

  breakout  - a daily CLOSE through the shelf inside the action window
              (wick rule: closes decide, wicks never do)
  retest    - after the break, price pulled back within 1.5% of the
              level and NO close has crossed back through — the entry
              the desk's whole retest doctrine is built on
  failed    - a close back through the level after the break; recorded,
              never surfaced as a candidate

No lookahead by construction: shelves are computed ONLY from bars
before the action window, so a level can never be justified by the
very move that broke it. Bearish rows (support breakdowns) are
computed and stored but surface as WARNINGS — structure shorts are
retired from entries (2026-08-08) and the index short study refused
four mechanisms (2026-08-23).

This is a SCREEN: per the measurement-harness doctrine it is graded by
alert-performance forward returns, never wired into arming.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.structure_screen")

MIN_TOUCHES_MAJOR = 3
HISTORY_BARS = 420        # ~20 months of dailies feed the shelves
BREAK_WINDOW = 12         # trading days scanned for the break/retest
RETEST_BAND = 0.015
MIN_PRICE = 3.0
MIN_DOLLAR_VOL = 3e6      # 20d avg close*volume
# First run (2026-08-23): the touch ranking crowned T-bill/bond ETFs —
# a cash fund drifting in a 20-cent range for two years is a 150-touch
# "shelf" to a touch counter. A level only means something if the
# instrument MOVES around it; the flat-score-bar lesson, again.
MIN_RANGE_PCT = 0.015     # 30d avg (high-low)/close
BUDGET_S = 25 * 60


def classify(level: float, kind: str, action_bars: list):
    """Pure lifecycle classifier for ONE major level against the action
    window. kind: 'resistance' (bullish break up) or 'support'
    (bearish breakdown). action_bars: [{date, high, low, close}]
    oldest-first, all AFTER the shelf window. Returns None (level never
    broken) or {state, break_date, retest_date}."""
    up = kind == "resistance"
    brk = None
    prev_inside = True     # shelf bars end on the un-broken side
    for b in action_bars:
        c = float(b["close"])
        crossed = c > level if up else c < level
        if brk is None:
            if crossed and prev_inside:
                brk = {"state": "breakout", "break_date": b["date"],
                       "retest_date": None}
            prev_inside = not crossed
            continue
        # post-break lifecycle
        if (c < level) if up else (c > level):
            brk["state"] = "failed"          # close back through: lost
            return brk
        touched = (float(b["low"]) <= level * (1 + RETEST_BAND)) if up \
            else (float(b["high"]) >= level * (1 - RETEST_BAND))
        if touched:
            brk["state"] = "retest"
            brk["retest_date"] = brk["retest_date"] or b["date"]
    return brk


def _weekly(bars: list) -> list:
    """ISO-week resample of daily dicts (high/low/close) for the weekly
    pivot pass. Completed weeks only — the current partial week never
    feeds a shelf (drop_partial doctrine)."""
    out, cur, key = [], None, None
    for b in bars:
        k = b["date"].isocalendar()[:2]
        if k != key:
            if cur:
                out.append(cur)
            cur = {"high": b["high"], "low": b["low"], "close": b["close"]}
            key = k
        else:
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
    # drop the in-progress week (cur) on purpose
    return out


def run_structure_screen() -> dict:
    """Nightly fleet pass over recorded daily bars. Idempotent per day."""
    from analysis.levels import _pivots, levels_from_points
    from screen.reversal_screen import _conn

    conn = _conn()
    t0 = time.time()
    scanned = kept = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker FROM (
                    SELECT ticker,
                           avg(close * COALESCE(volume, 0)) AS dv,
                           avg((high - low) / NULLIF(close, 0)) AS rng,
                           max(trade_date) AS last_d, max(close) AS px
                    FROM daily_prices
                    WHERE trade_date > CURRENT_DATE - INTERVAL '30 days'
                      AND high IS NOT NULL AND low IS NOT NULL
                    GROUP BY ticker) u
                WHERE dv >= %s AND px >= %s AND rng >= %s
                  AND last_d > CURRENT_DATE - INTERVAL '6 days'
                ORDER BY ticker""",
                (MIN_DOLLAR_VOL, MIN_PRICE, MIN_RANGE_PCT))
            universe = [r[0] for r in cur.fetchall()]
        today = dt.date.today()
        rows_out = []
        for tk in universe:
            if time.time() - t0 > BUDGET_S:
                log.warning(f"[structure] budget hit at {scanned}/"
                            f"{len(universe)} — partial run recorded.")
                break
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT trade_date, high, low, close FROM daily_prices
                    WHERE ticker=%s AND close IS NOT NULL
                      AND high IS NOT NULL AND low IS NOT NULL
                    ORDER BY trade_date DESC LIMIT %s""",
                    (tk, HISTORY_BARS + BREAK_WINDOW))
                bars = [{"date": r[0], "high": float(r[1]),
                         "low": float(r[2]), "close": float(r[3])}
                        for r in reversed(cur.fetchall())]
            scanned += 1
            if len(bars) < 120:
                continue
            shelf_bars = bars[:-BREAK_WINDOW]
            action = bars[-BREAK_WINDOW:]
            points = _pivots(shelf_bars, "1D", 3, 3)
            wk = _weekly(shelf_bars)
            if len(wk) >= 25:
                points += _pivots(wk, "1W", 2, 2)
            if not points:
                continue
            res = levels_from_points(points, shelf_bars,
                                     current_price=shelf_bars[-1]["close"])
            if "error" in res:
                continue
            last_close = action[-1]["close"]
            for lv in (res.get("resistance") or []) + (res.get("support") or []):
                if lv["touches"] < MIN_TOUCHES_MAJOR:
                    continue
                verdict = classify(lv["price"], lv["kind"], action)
                if verdict is None:
                    continue
                rows_out.append((
                    today, tk,
                    "bullish" if lv["kind"] == "resistance" else "bearish",
                    verdict["state"], lv["price"], lv["touches"],
                    lv["stars"], ",".join(lv["timeframes"]),
                    verdict["break_date"], verdict["retest_date"],
                    last_close,
                    round((last_close - lv["price"]) / lv["price"] * 100, 2)))
        if rows_out:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO structure_screen
                        (run_date, ticker, direction, state, level, touches,
                         stars, timeframes, break_date, retest_date,
                         last_close, dist_pct)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_date, ticker, level, direction)
                    DO NOTHING""", rows_out)
            conn.commit()
        kept = len(rows_out)
        log.info(f"[structure] {scanned} scanned, {kept} level-events "
                 f"({time.time()-t0:.0f}s).")
        return {"scanned": scanned, "events": kept}
    finally:
        conn.close()
