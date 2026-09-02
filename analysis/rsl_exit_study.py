"""
The RS-leader EXIT re-grade on the book's OWN entries (2026-09-02
evening). Found by the chase study's f=0 baseline: the live lifecycle —
struct stop on 5m CLOSES + 1% disaster TOUCH + 21-EMA trail after +1R —
grades -0.21/-0.27R by year-half on the 446 graded GO entries, while
hold-to-close on the SAME entries (rs_leader_events.r_close) grades
+0.38/+0.52R. The exits were graded on the tape-entry population
(hybrid_exit_study, 1m-gated entries) and ASSUMED to transfer to the
GO population. Assert the integration; never assume it.

VARIANTS (frozen before any number; every exit decided by the book's
own lifecycle_state with declared switches — one definition, no copy):
  book         as live: struct stop (close rule) + disaster + trail
               after +1R
  hold         no stop of any kind; exit at the bell (the study's
               graded trade, recomputed from the same bars)
  disaster     the 1% disaster TOUCH only; bell for survivors
  dis_trail    disaster + trail after +1R (GO risk); NO struct stop
  struct_bell  struct stop (close rule) + disaster; NO trail; bell
  wide5        struct = the lowest low of the FIVE 1m bars ending at
               the GO bar (the pullback's five-minute window, always at
               or under the book's pullback-bar stop) x (1 - STOP_BUFF);
               book rules otherwise (trail arms at +1R of the WIDER
               risk)

Outcomes per variant: out, exit_px, r_go (R on the GO risk unit so the
variants compare on ONE scale), r_own (R on the variant's own risk).
Readout: avg R capped +-10, both year-halves, per-name replication,
win rate. Caveats where numbers surface: closes as fills, no costs,
2-year 1m record, entries at the GO close.

Writes ONLY rsl_exit_events. Marker rsl_exit_v1 when rsleader_study_v1
exists and nothing is ungraded.
"""
import json
import logging
import time

from analysis.rs_leader_book import lifecycle_state
from analysis.rsleader_study import STOP_BUFF

log = logging.getLogger("watchtower.rsl_exit")

COMPLETE_MARKER = "rsl_exit_v1"
SOURCE_MARKER = "rsleader_study_v1"
BUDGET_S = 12 * 60
VARIANTS = ("book", "hold", "disaster", "dis_trail", "struct_bell", "wide5")


WIDE_N = 5


def wide5_stop(bars, i_go):
    """The lowest low of the WIDE_N 1m bars ending at the GO bar — the
    pullback's five-minute window, visible at the GO close."""
    lows = [b[3] for b in bars[max(0, i_go - WIDE_N + 1):i_go + 1]]
    return min(lows) * (1 - STOP_BUFF)


def sim_variants(bars, i_go, entry, stop):
    """Pure. Returns {variant: {out, exit_px, r_go, r_own}} — every
    decision made by the book's lifecycle_state under declared
    switches; 'hold' is the bell close with no rule at all."""
    risk = entry - stop
    if risk <= 0:
        return {}
    last_c = bars[-1][4]

    def pack(state, own_risk):
        if state is None or state["exit"] is None:
            o_, px = "eod", last_c
        else:
            o_, _ts, px = state["exit"]
        return {"out": o_, "exit_px": round(px, 4),
                "r_go": round((px - entry) / risk, 3),
                "r_own": round((px - entry) / own_risk, 3)}

    out = {
        "book": pack(lifecycle_state(bars, i_go, entry, stop), risk),
        "hold": pack(None, risk),
        "disaster": pack(lifecycle_state(bars, i_go, entry, stop,
                                         trail=False, struct_stop=False),
                         risk),
        "dis_trail": pack(lifecycle_state(bars, i_go, entry, stop,
                                          struct_stop=False), risk),
        "struct_bell": pack(lifecycle_state(bars, i_go, entry, stop,
                                            trail=False), risk),
    }
    s5 = wide5_stop(bars, i_go)
    if s5 < entry:
        out["wide5"] = pack(lifecycle_state(bars, i_go, entry, s5),
                            entry - s5)
        out["wide5"]["stop"] = round(s5, 4)
    else:
        out["wide5"] = {"out": "invalid"}      # window low at/above entry
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
                           AND NOT EXISTS (SELECT 1 FROM rsl_exit_events v
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
                log.info("[rsl-exit] budget hit; resuming.")
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
            variants = sim_variants(bars, i_go, float(entry), float(stop))
            if not variants:
                continue
            with conn.cursor() as c:
                c.execute("""INSERT INTO rsl_exit_events
                             (event_id, ticker, trade_date, variants)
                             VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                          (eid, tk, d, json.dumps(variants)))
            conn.commit()
            n += 1
        log.info("[rsl-exit] graded %d GO entries this pass.", n)
        return False
    finally:
        conn.close()
