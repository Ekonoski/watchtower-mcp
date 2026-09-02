"""
Trail-variant extension II (2026-09-02, research docket: pass-1 #11
Kalman filter as an exit trail, pass-2 P2-21 MAD volatility trail).
Same harness as trailvar_study: exits re-simulated on the SAME graded
ema_1m_gated long entries, apples-to-apples with the hybrid verdicts.

VARIANTS (frozen; both keep phase 1 = struct stop on 5m CLOSES + 1%
disaster TOUCH, both switch at a 1m high touching entry+1R):
  kalman_5m  after +1R: a 1-D Kalman level on the day-anchored 5m
             closes — process var q = (0.15*ATR)^2, measurement var
             r = (0.6*ATR)^2 with ATR = the 5m ATR14 at that bar
             (entry ATR as fallback). x0 = first 5m close, P0 = r.
             Exit on a completed 5m CLOSE below the filtered level.
             Stated honestly: with constant q/r the gain converges, so
             this is an adaptive-then-steady smoother about as fast as
             a 6-bar EMA — the question is whether a FASTER, variance-
             aware line beats the graded 21-EMA, not whether 'Kalman'
             is magic.
  mad_trail  after +1R: trail = (highest 1m high since entry) minus
             3.0 x MAD20, the median absolute deviation of the last 20
             completed 5m closes about their median; exit on a
             completed 5m CLOSE below the trail. k=3 and N=20 are
             DECLARED, not fitted.

Readout: beside hybrid + trailvar variants, all days + leader days,
year-halves, per-name replication, r capped +-10. Writes ONLY
trailvar2_events; marker trailvar2_v1 when hybridexit_v1 exists and
nothing is ungraded.
"""
import datetime as dt
import json
import logging
import statistics
import time

from analysis.hybrid_exit_study import _res5 as res5
from analysis.tapeentry_study import atr_series

log = logging.getLogger("watchtower.trailvar2")

COMPLETE_MARKER = "trailvar2_v1"
SOURCE_MARKER = "hybridexit_v1"
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
DISASTER_PCT = 0.01
KAL_Q = 0.15
KAL_R = 0.6
MAD_K = 3.0
MAD_N = 20
BUDGET_S = 12 * 60


def kalman_levels(closes5, atr5, atr_fallback):
    """Pure: the filtered level at every completed 5m block."""
    out = []
    x = p = None
    for j, z in enumerate(closes5):
        a = atr5[j] if j < len(atr5) and atr5[j] else atr_fallback
        if not a or a <= 0:
            out.append(x)
            continue
        q, r = (KAL_Q * a) ** 2, (KAL_R * a) ** 2
        if x is None:
            x, p = z, r
        else:
            p = p + q
            k = p / (p + r)
            x = x + k * (z - x)
            p = (1 - k) * p
        out.append(x)
    return out


def mad_series(closes5, n=MAD_N):
    """Pure: MAD of the last n closes at each block (None in warmup)."""
    out = []
    for j in range(len(closes5)):
        if j + 1 < n:
            out.append(None)
            continue
        w = closes5[j + 1 - n:j + 1]
        m = statistics.median(w)
        out.append(statistics.median(abs(v - m) for v in w))
    return out


def sim_variants(bars1, i_after, entry, struct, kal_map, mad_map):
    """Pure. kal_map/mad_map: 1m index of each COMPLETED 5m boundary ->
    value (None elsewhere / warmup). Returns {variant: {out, exit_px,
    bps, r}}."""
    risk = entry - struct
    if risk <= 0:
        return {}
    arm_px = entry + risk
    disaster = entry * (1 - DISASTER_PCT)
    last_c = bars1[-1][4]
    state = {"kalman_5m": {"done": None}, "mad_trail": {"done": None}}
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
        if i not in kal_map:                    # not a completed 5m close
            continue
        if not armed:
            for v in state.values():
                if v["done"] is None and c < struct:
                    v["done"] = ("stopped", c)
            continue
        s = state["kalman_5m"]
        x = kal_map.get(i)
        if s["done"] is None and x is not None and c < x:
            s["done"] = ("trail", c)
        s = state["mad_trail"]
        mad = mad_map.get(i)
        if s["done"] is None and mad is not None and c < hh - MAD_K * mad:
            s["done"] = ("trail", c)
    out = {}
    for name, s in state.items():
        o_, px = s["done"] if s["done"] else ("eod", last_c)
        out[name] = {"out": o_, "exit_px": round(px, 4),
                     "bps": round((px / entry - 1) * 1e4, 1),
                     "r": round((px - entry) / risk, 2)}
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
                           AND NOT EXISTS (SELECT 1 FROM trailvar2_events v
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
                log.info("[trailvar2] budget hit; resuming.")
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
            atr5 = atr_series(bars5, 14)
            ets_et = ets.astimezone(et)
            gov = None
            for j in range(len(bars5)):
                if bars5[j][0] <= ets_et:
                    gov = j
                else:
                    break
            atr_entry = atr5[gov] if gov is not None else None
            kal = kalman_levels(c5, atr5, atr_entry)
            mad = mad_series(c5)
            kal_map = {last5[j]: kal[j] for j in range(len(bars5))}
            mad_map = {last5[j]: mad[j] for j in range(len(bars5))}
            i_after = next((i for i, b in enumerate(bars1)
                            if b[0] > ets_et), len(bars1))
            variants = sim_variants(bars1, i_after, float(entry),
                                    float(struct), kal_map, mad_map)
            if not variants:
                continue
            with conn.cursor() as c:
                c.execute("""INSERT INTO trailvar2_events
                             (event_id, ticker, trade_date, variants)
                             VALUES (%s,%s,%s,%s)
                             ON CONFLICT DO NOTHING""",
                          (eid, tk, d, json.dumps(variants)))
            conn.commit()
            n += 1
        log.info("[trailvar2] graded %d entries this pass.", n)
        return False
    finally:
        conn.close()
