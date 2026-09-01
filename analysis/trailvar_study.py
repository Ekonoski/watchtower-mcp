"""
Trail-variant extension (2026-09-01, from the pass-2 scan's exit
candidates — chandelier and the efficiency 'when to trail' gate,
P2-27/P2-24): two more exits re-simulated on the SAME graded
ema_1m_gated long entries the hybrid study used, apples-to-apples
with its verdicts (fixed / be_1r / trail_1r_5mlow / trail_1r_ema21 /
tgt2 already in hybridexit_events).

VARIANTS (frozen; both keep phase 1 = struct stop on 5m CLOSES + 1%
disaster TOUCH, both switch at a 1m high touching entry+1R):
  chand_25   after +1R: ratchet = (highest 1m high since entry) minus
             2.5 x ATR14 of the day-anchored 5m frame FROZEN at entry;
             exit on a completed 5m CLOSE below the ratchet.
  er_gate21  after +1R: the 21-EMA 5m-close trail, but a trail
             decision only COUNTS when the market has earned it —
             Kaufman efficiency over the last 12 completed 5m bars
             (|net move| / path) >= 0.35 at the deciding bar. Gate
             off = hold (disaster still live). Threshold 0.35 is
             DECLARED, not fitted; the question is whether gating
             helps at all, graded in both halves.

Readout: beside the hybrid variants, all days + leader days,
year-halves, r capped +-10. Writes ONLY trailvar_events; marker
trailvar_v1 when hybridexit_v1 exists and nothing is ungraded.
"""
import datetime as dt
import json
import logging
import time

from analysis.hybrid_exit_study import _ema as ema5
from analysis.hybrid_exit_study import _res5 as res5
from analysis.tapeentry_study import atr_series

log = logging.getLogger("watchtower.trailvar")

COMPLETE_MARKER = "trailvar_v1"
SOURCE_MARKER = "hybridexit_v1"
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
DISASTER_PCT = 0.01
ER_MIN = 0.35
CHAND_K = 2.5
BUDGET_S = 12 * 60


def sim_variants(bars1, i_after, entry, struct, atr_entry, e21_map, er_map):
    """Pure. bars1 = the day's 1m bars; e21_map/er_map: 1m index ->
    value at each COMPLETED 5m boundary (None elsewhere). Returns
    {variant: {out, exit_px, bps, r}}."""
    risk = entry - struct
    if risk <= 0 or not atr_entry:
        return {}
    arm_px = entry + risk
    disaster = entry * (1 - DISASTER_PCT)
    last_c = bars1[-1][4]
    state = {"chand_25": {"done": None}, "er_gate21": {"done": None}}
    armed = False
    hh = entry
    for i in range(i_after, len(bars1)):
        ts, o, h, l, c = bars1[i]
        hh = max(hh, h)
        for v in state.values():
            if v["done"] is None and l <= disaster:
                v["done"] = ("disaster", disaster)
        if h >= arm_px:
            armed = True
        e21 = e21_map.get(i)
        if e21 is not None:                     # a completed 5m close
            if not armed:
                for v in state.values():
                    if v["done"] is None and c < struct:
                        v["done"] = ("stopped", c)
            else:
                s = state["chand_25"]
                if s["done"] is None and c < hh - CHAND_K * atr_entry:
                    s["done"] = ("trail", c)
                s = state["er_gate21"]
                er = er_map.get(i)
                if (s["done"] is None and er is not None
                        and er >= ER_MIN and c < e21):
                    s["done"] = ("trail", c)
    out = {}
    for name, s in state.items():
        o_, px = s["done"] if s["done"] else ("eod", last_c)
        bps = (px / entry - 1) * 1e4
        out[name] = {"out": o_, "exit_px": round(px, 4),
                     "bps": round(bps, 1), "r": round((px - entry) / risk, 2)}
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
                                e.entry_px, e.struct_px
                         FROM tapeentry_events e
                         WHERE e.family='ema_1m_gated' AND e.direction='long'
                           AND e.struct_px IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM trailvar_events v
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
        cur_key, bars1 = None, []
        n = 0
        for eid, tk, d, ets, entry, struct in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[trailvar] budget hit; resuming.")
                return False
            if (tk, d) != cur_key:
                table = "mag7_1m_bars" if tk in MAG7 else "liquid_1m_bars"
                with conn.cursor() as c:
                    c.execute(f"""SELECT ts, open, high, low, close
                                  FROM {table}
                                  WHERE ticker=%s AND trade_date=%s
                                  ORDER BY ts""", (tk, d))
                    bars1 = [(ts.astimezone(et), float(o), float(h),
                              float(l), float(cl))
                             for ts, o, h, l, cl in c.fetchall()]
                cur_key = (tk, d)
            if len(bars1) < 60:
                continue
            bars5, last5 = res5(bars1)
            c5 = [b[4] for b in bars5]
            e21_5 = ema5(c5, 21)
            atr5 = atr_series(bars5, 14)
            er = [None] * len(bars5)
            for j in range(12, len(bars5)):
                path = sum(abs(c5[k] - c5[k - 1]) for k in range(j - 11, j + 1))
                er[j] = abs(c5[j] - c5[j - 12]) / path if path > 0 else None
            e21_map = {last5[j]: e21_5[j] for j in range(len(bars5))}
            er_map = {last5[j]: er[j] for j in range(len(bars5))}
            ets_et = ets.astimezone(et)
            i_after = next((i for i, b in enumerate(bars1)
                            if b[0] > ets_et), len(bars1))
            gov = None
            for j in range(len(bars5)):
                if bars5[j][0] <= ets_et:
                    gov = j
                else:
                    break
            atr_entry = atr5[gov] if gov is not None else None
            variants = sim_variants(bars1, i_after, float(entry),
                                    float(struct), atr_entry, e21_map, er_map)
            if not variants:
                continue
            with conn.cursor() as c:
                c.execute("""INSERT INTO trailvar_events
                             (event_id, ticker, trade_date, variants)
                             VALUES (%s,%s,%s,%s)
                             ON CONFLICT DO NOTHING""",
                          (eid, tk, d, json.dumps(variants)))
            conn.commit()
            n += 1
        log.info("[trailvar] graded %d entries this pass.", n)
        return False
    finally:
        conn.close()
