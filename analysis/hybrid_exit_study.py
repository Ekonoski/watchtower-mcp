"""
The hybrid-exit study (2026-08-31 late — Eric: "What if we ran it on a
2:1 ratio or a trailing stop?" then "Why can't we do the hybrid test
now?"). Same entries the tape-entry study graded (ema_1m_gated longs,
the RS-leader trade's mechanic); only the EXIT is re-simulated, so the
comparison is apples-to-apples by construction.

VARIANTS (frozen before any number; every one keeps the 1% disaster
cap, exiting on TOUCH — the tail guard never waits for a close):
  fixed           the graded baseline in-frame: struct stop, exit on a
                  completed 5m CLOSE through, else hold to the close.
  be_1r           fixed until a 1m HIGH touches entry + 1R (risk =
                  entry - struct level); then the stop LEVEL becomes
                  the entry (breakeven), still 5m-close rule.
  trail_1r_5mlow  fixed until +1R; then the stop ratchets to each
                  completed 5m bar's low (never down), exit on a 5m
                  CLOSE through the ratchet.
  trail_1r_ema21  fixed until +1R; then exit on a 5m CLOSE below the
                  5m 21 EMA.
  tgt2            the refused 2R bracket, kept as the reference:
                  first-touch 2R target vs struct stop (touch), same-
                  bar both-touch = stopped (conservative).

Outcomes per variant: out, exit_px, bps, r (per struct risk). Readout
cuts: all entries and leader-days-only (join rs_leader_events), year-
halves. Caveats as ever: closes as fills, no costs, 1m granularity;
r capped +-10 at readout (the outlier rule). One stated divergence:
the trail_1r_ema21 leg uses a DAY-anchored 5m 21 EMA (the stop grid
already graded the continuous-EMA trail; this variant is a
cross-check, not the primary question).

Writes ONLY hybridexit_events; resumes by NOT EXISTS per source event;
marker hybridexit_v1 when tapeentry_study_v1 exists and nothing is
left to grade.
"""
import datetime as dt
import json
import logging
import time

log = logging.getLogger("watchtower.hybridexit")

COMPLETE_MARKER = "hybridexit_v1"
SOURCE_MARKER = "tapeentry_study_v1"
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
DISASTER_PCT = 0.01
BUDGET_S = 15 * 60


def _res5(bars):
    """(bars5, last1m_idx) fixed-anchor from 9:30 — the tape-entry frame."""
    out, last = [], []
    key = None
    o = h = l = c = None
    for i, (ts, bo, bh, bl, bc) in enumerate(bars):
        k = (ts.hour - 9) * 60 + ts.minute - 30
        k = k // 5
        if k != key:
            if key is not None:
                out.append((bars[last[-1]][0], o, h, l, c))
            key, o, h, l, c = k, bo, bh, bl, bc
            last.append(i)
        else:
            h = max(h, bh)
            l = min(l, bl)
            c = bc
            last[-1] = i
    if key is not None:
        out.append((bars[last[-1]][0], o, h, l, c))
    return out, last


def _ema(vals, n):
    out, k = [], 2 / (n + 1)
    e = vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def sim_hybrid(bars1, i_after, entry, struct, e21_5_by_min):
    """Pure. bars1 = the DAY's 1m bars; i_after = first 1m index after
    entry; struct = stop level; e21_5_by_min[i] = the day-anchored 5m
    21 EMA value of the 5m bar that COMPLETES at 1m index i (None on
    non-boundary minutes). Long only (the graded trade). Returns
    {variant: {out, exit_px, bps, r}}."""
    risk = entry - struct
    if risk <= 0:
        return {}
    arm_px = entry + risk
    disaster = entry * (1 - DISASTER_PCT)
    tgt = entry + 2 * risk
    last_c = bars1[-1][4]

    state = {
        "fixed": {"stop": struct, "armed": False, "done": None},
        "be_1r": {"stop": struct, "armed": False, "done": None},
        "trail_1r_5mlow": {"stop": struct, "armed": False, "done": None},
        "trail_1r_ema21": {"stop": struct, "armed": False, "done": None},
        "tgt2": {"done": None},
    }
    cur5_low = None
    for i in range(i_after, len(bars1)):
        ts, o, h, l, c = bars1[i]
        cur5_low = l if cur5_low is None else min(cur5_low, l)
        # disaster cap: touch, all close-rule variants
        for v in ("fixed", "be_1r", "trail_1r_5mlow", "trail_1r_ema21"):
            s = state[v]
            if s["done"] is None and l <= disaster:
                s["done"] = ("disaster", disaster)
        # tgt2: conservative first-touch bracket
        s = state["tgt2"]
        if s["done"] is None:
            if l <= struct:
                s["done"] = ("stopped", struct)
            elif h >= tgt:
                s["done"] = ("target2", tgt)
        # arming on a 1m HIGH touch of entry + 1R
        if h >= arm_px:
            for v in ("be_1r", "trail_1r_5mlow", "trail_1r_ema21"):
                s = state[v]
                if s["done"] is None and not s["armed"]:
                    s["armed"] = True
                    if v == "be_1r":
                        s["stop"] = max(s["stop"], entry)
        # completed 5m boundary: close-rule decisions + ratchets
        e21 = e21_5_by_min.get(i)
        if e21 is not None:
            close5 = c
            for v in ("fixed", "be_1r", "trail_1r_5mlow"):
                s = state[v]
                if s["done"] is None and close5 < s["stop"]:
                    s["done"] = ("stopped", close5)
            s = state["trail_1r_ema21"]
            if s["done"] is None:
                if s["armed"]:
                    if close5 < e21:
                        s["done"] = ("trail_exit", close5)
                elif close5 < s["stop"]:
                    s["done"] = ("stopped", close5)
            # ratchet AFTER the decision: the just-completed bar's low
            # protects from the NEXT bar on, never itself
            s = state["trail_1r_5mlow"]
            if s["done"] is None and s["armed"]:
                s["stop"] = max(s["stop"], cur5_low)
            cur5_low = None
    out = {}
    for v, s in state.items():
        if s["done"] is None:
            o_, px = "eod", last_c
        else:
            o_, px = s["done"]
        bps = (px / entry - 1) * 1e4
        out[v] = {"out": o_, "exit_px": round(px, 4),
                  "bps": round(bps, 1), "r": round((px - entry) / risk, 2)}
    return out


def run() -> bool:
    from screen.reversal_screen import _conn
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
                           AND NOT EXISTS (SELECT 1 FROM hybridexit_events h
                                           WHERE h.event_id = e.id)
                         ORDER BY e.ticker, e.trade_date""")
            todo = c.fetchall()
        if not todo:
            if src_done:
                with conn.cursor() as c:
                    c.execute(
                        "INSERT INTO scheduler_job_claims (job_name, "
                        "run_date) VALUES (%s, CURRENT_DATE) "
                        "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
                conn.commit()
                log.info("[hybridexit] complete — marker %s.",
                         COMPLETE_MARKER)
            return True
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        cur_tk, cur_d = None, None
        bars1 = []
        n = 0
        for eid, tk, d, ets, entry, struct in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[hybridexit] budget hit; resuming.")
                return False
            if (tk, d) != (cur_tk, cur_d):
                table = ("mag7_1m_bars" if tk in MAG7 else "liquid_1m_bars")
                with conn.cursor() as c:
                    c.execute(f"""SELECT ts, open, high, low, close
                                  FROM {table}
                                  WHERE ticker=%s AND trade_date=%s
                                  ORDER BY ts""", (tk, d))
                    bars1 = [(ts.astimezone(et), float(o), float(h),
                              float(l), float(cl))
                             for ts, o, h, l, cl in c.fetchall()]
                cur_tk, cur_d = tk, d
            if len(bars1) < 60:
                continue
            bars5, last5 = _res5(bars1)
            e21_5 = _ema([b[4] for b in bars5], 21)
            e21_by_min = {last5[j]: e21_5[j] for j in range(len(bars5))}
            ets_et = ets.astimezone(et)
            i_after = next((i for i, b in enumerate(bars1)
                            if b[0] > ets_et), len(bars1))
            variants = sim_hybrid(bars1, i_after, float(entry),
                                  float(struct), e21_by_min)
            if not variants:
                continue
            with conn.cursor() as c:
                c.execute("""INSERT INTO hybridexit_events
                             (event_id, ticker, trade_date, variants)
                             VALUES (%s,%s,%s,%s)
                             ON CONFLICT DO NOTHING""",
                          (eid, tk, d, json.dumps(variants)))
            conn.commit()
            n += 1
        log.info("[hybridexit] graded %d entries this pass.", n)
        return False
    finally:
        conn.close()
