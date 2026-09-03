"""
Risk management as the edge (2026-09-03, 8:40 AM — Eric: "if the coin
flip is a 50% ratio then isn't the bigger edge in risk management?").

QUESTION: on the coin-flip entry populations, does an asymmetric exit
with the risk unit set at the trade's REAL excursion — no stop inside
the noise band, a hard disaster line, a trail once the trade proves
itself — lift a ~50% entry into positive expectancy in both halves?

SPEC (frozen before any number; every exit decided by the book's own
lifecycle_state under its declared switches — one definition):
  populations
    tapeentry   every LONG entry family in tapeentry_events on the 11
                liquid names (the unconditioned coin-flip set: ema_1m_
                gated, ema8_5m, ema21_5m, orb_chase/rt, pdh_chase/rt).
                Shorts are NOT graded — lifecycle_state is long-only
                and a mirror would be a second definition.
    rsl_go      the 446 graded RS-leader GO entries.
  variants (R reported on ONE unit for all: 1.0% of entry; bps beside)
    hold        no rule; the bell
    dis1        the 1% disaster TOUCH only; bell
    dt_050      disaster touch + 21-EMA trail after +0.5%; no struct stop
    dt_100      disaster touch + trail after +1.0%; no struct stop
    dt_150      disaster touch + trail after +1.5%; no struct stop
  readout   avg R (capped +-10) and bps, win rate, both year-halves,
            per family and per name. Caveats: closes as fills, no
            costs, 1m record, entries at the population's own prices.

Writes ONLY riskmgmt_events. Marker riskmgmt_v1 when tapeentry_study_v1
and rsleader_study_v1 exist and nothing is ungraded.
"""
import json
import logging
import time

from analysis.rs_leader_book import lifecycle_state

log = logging.getLogger("watchtower.riskmgmt")

COMPLETE_MARKER = "riskmgmt_v1"
SOURCE_MARKERS = ("tapeentry_study_v1", "rsleader_study_v1")
BUDGET_S = 12 * 60
UNIT_PCT = 0.01
ARMS = (("dt_050", 0.005), ("dt_100", 0.010), ("dt_150", 0.015))


def sim_variants(bars, i_entry, entry):
    """Pure. bars = the day's 1m RTH bars (ts,o,h,l,c); i_entry = the
    entry bar's index; every variant's R is on the 1% unit."""
    unit = entry * UNIT_PCT
    last_c = bars[-1][4]

    def pack(state):
        if state is None or state["exit"] is None:
            o_, px = "eod", last_c
        else:
            o_, _ts, px = state["exit"]
        return {"out": o_, "exit_px": round(px, 4),
                "r": round((px - entry) / unit, 3),
                "bps": round((px / entry - 1) * 1e4, 1)}

    out = {"hold": pack(None),
           "dis1": pack(lifecycle_state(bars, i_entry, entry, entry * (1 - UNIT_PCT),
                                        trail=False, struct_stop=False))}
    for name, arm in ARMS:
        out[name] = pack(lifecycle_state(bars, i_entry, entry, entry * (1 - arm),
                                         struct_stop=False))
    return out


def _grade(conn, source, todo, bars_table, et, t0):
    cur_key, bars = None, []
    n = 0
    for eid, tk, d, ets, entry in todo:
        if time.time() - t0 > BUDGET_S:
            log.info("[riskmgmt] budget hit (%s); resuming.", source)
            return n, False
        if (tk, d) != cur_key:
            with conn.cursor() as c:
                c.execute(f"""SELECT ts, open, high, low, close
                              FROM {bars_table}
                              WHERE ticker=%s AND trade_date=%s
                              ORDER BY ts""", (tk, d))
                bars = [(ts.astimezone(et), float(o), float(h), float(l),
                         float(cl)) for ts, o, h, l, cl in c.fetchall()]
            cur_key = (tk, d)
        if len(bars) < 60:
            continue
        ets_et = ets.astimezone(et)
        i = next((k for k, b in enumerate(bars) if b[0] >= ets_et), None)
        if i is None or i >= len(bars) - 1:
            continue
        variants = sim_variants(bars, i, float(entry))
        with conn.cursor() as c:
            c.execute("""INSERT INTO riskmgmt_events
                         (source, event_id, ticker, trade_date, variants)
                         VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                      (source, eid, tk, d, json.dumps(variants)))
        conn.commit()
        n += 1
    return n, True


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
            c.execute("SELECT count(*) FROM scheduler_job_claims WHERE job_name = ANY(%s)",
                      (list(SOURCE_MARKERS),))
            src_done = c.fetchone()[0] == len(SOURCE_MARKERS)
            c.execute("""SELECT t.id, t.ticker, t.trade_date, t.entry_ts, t.entry_px
                         FROM tapeentry_events t
                         WHERE t.direction='long' AND t.entry_ts IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM riskmgmt_events v
                                           WHERE v.source='tapeentry' AND v.event_id=t.id)
                         ORDER BY t.ticker, t.trade_date""")
            todo_tape = c.fetchall()
            c.execute("""SELECT e.id, e.ticker, e.trade_date, e.entry_ts, e.entry_px
                         FROM rs_leader_events e
                         WHERE e.role='leader' AND e.entry_kind='go_pullback'
                           AND e.entry_ts IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM riskmgmt_events v
                                           WHERE v.source='rsl_go' AND v.event_id=e.id)
                         ORDER BY e.ticker, e.trade_date""")
            todo_go = c.fetchall()
        if not todo_tape and not todo_go:
            if src_done:
                with conn.cursor() as c:
                    c.execute("INSERT INTO scheduler_job_claims (job_name, "
                              "run_date) VALUES (%s, CURRENT_DATE) "
                              "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
                conn.commit()
            return True
        n1, ok = _grade(conn, "rsl_go", todo_go, "mag7_1m_bars", et, t0)
        n2 = 0
        if ok:
            n2, ok = _grade(conn, "tapeentry", todo_tape, "liquid_1m_bars", et, t0)
        log.info("[riskmgmt] graded %d GO + %d tape entries this pass.", n1, n2)
        return False
    finally:
        conn.close()
