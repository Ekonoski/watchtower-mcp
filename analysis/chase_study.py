"""
The chase-premium tolerance grade (2026-09-02, pre-registered — Eric
skipped META because "the candle was too far away"; the right skip is
a number, not a feel).

QUESTION: how does the RS-leader trade's expectancy decay when the
fill lands ABOVE the GO bar's close?

SPEC (frozen before any number):
  entries   every graded leader GO in rs_leader_events (role='leader',
            entry_ts/entry_px/stop_px present).
  variants  fill = GO close + f x risk, f in (0, 0.10, 0.25, 0.50, 1.00),
            where risk = GO close - stop. The fill is only granted if
            a 1m bar within the next 3 minutes actually TRADED there
            (high >= fill); otherwise 'no_fill' — a skipped trade, not
            a zero. Stop UNCHANGED (it belongs to the pullback bar);
            the lifecycle is the book's own (rs_leader_book.
            lifecycle_state — disaster/stop/trail/eod), run from the
            fill bar with entry = the fill price, so R is measured
            against the WIDER risk the chaser actually took.
  outcomes  per f: R to exit, exit reason, fill delay (minutes).
  readout   avg R (capped +-10) by f, both year-halves, per-name
            replication; the tolerance line printed on the 🎯 is the
            largest f whose expectancy stays within ~25% of f=0 in
            BOTH halves. Caveats where numbers surface: closes as
            fills, no costs, 2-year record.
Writes ONLY chase_events. Marker chase_v1 when rsleader_study_v1 exists
and nothing is ungraded.

CORRECTION (same evening, first readout): the first pass graded every
leader row, and half of rs_leader_events' leader rows are the study's
`no_pullback_945` control (own-the-leader-from-9:45, no GO, risk ~1.5%)
— NOT the trade. The population is `entry_kind='go_pullback'` only;
the 446 no-pullback rows were deleted. And the f=0 baseline is the
BOOK'S OWN LIFECYCLE on its own entries — which this study was the
first to grade: -0.21/-0.27R by half vs hold-to-close +0.38/+0.52R on
the same entries. That finding has its own study (rsl_exit_study).
"""
import json
import logging
import time

from analysis.rs_leader_book import lifecycle_state

log = logging.getLogger("watchtower.chase")

COMPLETE_MARKER = "chase_v1"
SOURCE_MARKER = "rsleader_study_v1"
FRACTIONS = (0.0, 0.10, 0.25, 0.50, 1.00)
FILL_WINDOW = 3          # minutes after the GO bar in which the fill must print
BUDGET_S = 12 * 60


def simulate_fills(bars, i_go, go_close, stop):
    """Pure. For each premium fraction, find the first bar in the fill
    window that trades through the fill price, then run the book's
    lifecycle from that bar with entry = fill. Returns {f: {...}}."""
    risk = go_close - stop
    out = {}
    if risk <= 0:
        return out
    for f in FRACTIONS:
        fill = go_close + f * risk
        i_fill = None
        for i in range(i_go + 1, min(i_go + 1 + FILL_WINDOW, len(bars))):
            if bars[i][2] >= fill:              # high trades through
                i_fill = i
                break
        if f == 0.0:
            i_fill = i_go                       # the graded entry itself
        if i_fill is None:
            out[f"{f:.2f}"] = {"out": "no_fill"}
            continue
        state = lifecycle_state(bars, i_fill, fill, stop)
        if state["exit"] is None:
            px, reason = bars[-1][4], "eod"
        else:
            reason, _ts, px = state["exit"]
        new_risk = fill - stop
        out[f"{f:.2f}"] = {"out": reason, "fill": round(fill, 4),
                           "delay_min": i_fill - i_go,
                           "r": round((px - fill) / new_risk, 3),
                           "r_go_basis": round((px - fill) / risk, 3)}
    return out


def run() -> bool:
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (SOURCE_MARKER,))
            src_done = c.fetchone() is not None
            c.execute("""SELECT e.id, e.ticker, e.trade_date, e.entry_ts,
                                e.entry_px, e.stop_px
                         FROM rs_leader_events e
                         WHERE e.role='leader' AND e.entry_kind='go_pullback'
                           AND e.entry_ts IS NOT NULL
                           AND e.stop_px IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM chase_events v
                                           WHERE v.event_id = e.id)
                         ORDER BY e.ticker, e.trade_date""")
            todo = c.fetchall()
        if not todo:
            if src_done:
                with conn.cursor() as c:
                    c.execute("INSERT INTO scheduler_job_claims (job_name, "
                              "run_date) VALUES (%s, CURRENT_DATE) "
                              "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
                conn.commit()
            return True
        cur_key, bars = None, []
        n = 0
        for eid, tk, d, ets, entry, stop in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[chase] budget hit; resuming.")
                return False
            if (tk, d) != cur_key:
                with conn.cursor() as c:
                    c.execute("""SELECT ts, open, high, low, close
                                 FROM mag7_1m_bars
                                 WHERE ticker=%s AND trade_date=%s
                                 ORDER BY ts""", (tk, d))
                    bars = [(ts.astimezone(et), float(o), float(h),
                             float(l), float(cl))
                            for ts, o, h, l, cl in c.fetchall()]
                cur_key = (tk, d)
            if len(bars) < 60:
                continue
            ets_et = ets.astimezone(et)
            i_go = next((i for i, b in enumerate(bars)
                         if b[0] >= ets_et), None)
            if i_go is None:
                continue
            fills = simulate_fills(bars, i_go, float(entry), float(stop))
            if not fills:
                continue
            with conn.cursor() as c:
                c.execute("""INSERT INTO chase_events
                             (event_id, ticker, trade_date, fills)
                             VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                          (eid, tk, d, json.dumps(fills)))
            conn.commit()
            n += 1
        log.info("[chase] graded %d entries this pass.", n)
        return False
    finally:
        conn.close()
