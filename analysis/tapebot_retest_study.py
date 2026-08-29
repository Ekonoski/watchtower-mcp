"""
The Tape Bot retest-machine study (2026-08-29 — Eric, the night the
Tape Bot went live on his charts: "this indicator could be a game
changer for my day trading. Should we add a version of it to our
autonomous trading?"). Doctrine answer: through the gate, not around
it — the machine's signals grade on the stored record before any book
trades them.

Pre-registered spec, frozen before any number:

  machine     the Pine v2.2 state machine ported VERBATIM (same
              constants, same per-bar evaluation order): a confirmed
              close through the nearest level arms BROKE; >=1 bar
              later it becomes WAIT RETEST; a touch at the +/-0.15%
              band resolves RETEST BULL/BEAR on whether the close
              holds the level (a wick through is not proof); three
              more holding bars make HELD; LOST/RECLAIMED/RETEST FAIL
              or 40 bars kill it; state dies at each day boundary.
              Nearest-level selection within 1.0% of price, exactly
              like the script's lvlPct default.
  levels      phase 1: PDH/PDL from the prior day's stored RTH bars.
              ONH/ONL is a DECLARED HOLE — index_intraday_bars is
              RTH-only, so overnight levels need a premarket backfill
              (phase 2 if phase 1 grades).
  entries     the signal bar's close on each FRESH retest_bull /
              retest_bear / held_bull / held_bear — exactly the bar
              the Discord alert fires on. Shorts are recorded for the
              record (expected refusal; every prior short study failed).
  outcomes    to the same-day TRUE close (the 15:45 bar's close) in
              bps, MFE/MAE beside it, all in the trade's direction.
              Time-of-day rides in signal_ts for the 10:30 cut at
              readout (the 9:45 flush is known chop; the question is
              whether the machine's forced wait clears it).
  bar         era split 2005-2015 vs 2016-2026 AND QQQ replication —
              the same bar the day-bias long side cleared. Grades ->
              a small audition book per harness doctrine; fails ->
              the Tape Bot stays eyes.

Data: index_intraday_bars, SPY 2005-> and QQQ 2011-> (IWM excluded —
vendor-holey, per the day-bias record). Stored bars are completed
15m RTH bars, so every decision here is a confirmed close by
construction, matching canVote on an equity chart.

Writes ONLY tapebot_retest_events; marker-retired one-shot
(tapebot_retest_v1 in scheduler_job_claims); idempotent via the
events table's natural unique key.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.tapebot_retest")

COMPLETE_MARKER = "tapebot_retest_v1"
TICKERS = ("SPY", "QQQ")
BAND_PCT = 0.0015          # the Pine touch band
LVL_PCT = 1.0              # nearest-level search width, % of price
HELD_AGE = 3               # stAge > 3 promotes RETEST -> HELD
TIMEOUT = 40               # bars before an armed level is abandoned
BUDGET_S = 25 * 60

FRESH = ("RETEST BULL", "RETEST BEAR", "HELD BULL", "HELD BEAR")
_EVENT_KEY = {"RETEST BULL": ("retest_bull", "long"),
              "RETEST BEAR": ("retest_bear", "short"),
              "HELD BULL": ("held_bull", "long"),
              "HELD BEAR": ("held_bear", "short")}


def new_state():
    return {"st": "IDLE", "lvl": None, "lvl_nm": None, "age": 0}


def step(state, bar, prev_close, levels):
    """One confirmed bar through the Pine machine. bar = (o, h, l, c);
    levels = [(name, px), ...]; prev_close = the prior bar's close
    (continuous across the day boundary, as on a chart). Mutates
    state, returns the list of FRESH signal states this bar produced
    (subset of FRESH), same as the script's alert condition."""
    o, h, l, c = bar
    st0 = state["st"]
    state["age"] += 1

    # Nearest level within the search band (the script's lvlPct)
    near_nm, near_px, best = None, None, c * LVL_PCT / 100.0
    for nm, px in levels:
        if px is None:
            continue
        d = abs(c - px)
        if d <= best:
            best, near_nm, near_px = d, nm, px

    if near_px is not None and prev_close is not None:
        broke_up = c > near_px and prev_close <= near_px
        broke_dn = c < near_px and prev_close >= near_px
        if broke_up or broke_dn:
            state["st"] = "BROKE UP" if broke_up else "BROKE DOWN"
            state["lvl"] = near_px
            state["lvl_nm"] = near_nm
            state["age"] = 0

    if state["lvl"] is not None:
        lvl = state["lvl"]
        band = c * BAND_PCT
        touching = l <= lvl + band and h >= lvl - band
        if state["st"] in ("BROKE UP", "BROKE DOWN") and state["age"] >= 1:
            state["st"] = ("WAIT RETEST UP" if state["st"] == "BROKE UP"
                           else "WAIT RETEST DN")
        if state["st"] == "WAIT RETEST UP" and touching:
            state["st"] = "RETEST BULL" if c > lvl else "RETEST FAIL"
        if state["st"] == "WAIT RETEST DN" and touching:
            state["st"] = "RETEST BEAR" if c < lvl else "RECLAIMED"
        if state["st"] == "RETEST BULL":
            state["st"] = (("HELD BULL" if state["age"] > HELD_AGE
                            else "RETEST BULL") if c > lvl else "LOST")
        if state["st"] == "RETEST BEAR":
            state["st"] = (("HELD BEAR" if state["age"] > HELD_AGE
                            else "RETEST BEAR") if c < lvl else "RECLAIMED")
        if state["st"] in ("RETEST FAIL", "LOST", "RECLAIMED") \
                or state["age"] > TIMEOUT:
            state["st"] = "IDLE"
            state["lvl"] = None
            state["lvl_nm"] = None

    return [state["st"]] if (state["st"] != st0 and state["st"] in FRESH) \
        else []


def _grade_day(day_bars, i_sig, entry, direction):
    """Outcomes from the signal bar to the day's true close, in the
    trade's direction (bps). day_bars = [(ts, o, h, l, c), ...]."""
    close_px = day_bars[-1][4]
    seg = day_bars[i_sig + 1:]
    sign = 1.0 if direction == "long" else -1.0
    fwd = sign * (close_px / entry - 1) * 1e4
    if seg:
        hi = max(b[2] for b in seg)
        lo = min(b[3] for b in seg)
        mfe = (hi / entry - 1) * 1e4 if direction == "long" \
            else (1 - lo / entry) * 1e4
        mae = (lo / entry - 1) * 1e4 if direction == "long" \
            else (1 - hi / entry) * 1e4
    else:
        mfe, mae = 0.0, 0.0
    return close_px, round(fwd, 2), round(mfe, 2), round(mae, 2), len(seg)


def _one_ticker(conn, tk):
    with conn.cursor() as c:
        c.execute("""SELECT ts, trade_date, open, high, low, close
                     FROM index_intraday_bars WHERE ticker=%s
                     ORDER BY ts""", (tk,))
        rows = c.fetchall()
    if len(rows) < 100:
        log.warning("[tapebot-retest] %s: only %d bars — skipped as a hole.",
                    tk, len(rows))
        return 0
    # Group into days, keeping the continuous bar sequence.
    days = []                          # [(date, [(ts,o,h,l,c), ...])]
    for ts, d, o, h, l, cl in rows:
        bar = (ts, float(o), float(h), float(l), float(cl))
        if not days or days[-1][0] != d:
            days.append((d, [bar]))
        else:
            days[-1][1].append(bar)

    inserted = 0
    events = []
    prev_close = None
    for di in range(1, len(days)):
        pd_bars = days[di - 1][1]
        pdh = max(b[2] for b in pd_bars)
        pdl = min(b[3] for b in pd_bars)
        levels = [("PDH", pdh), ("PDL", pdl)]
        d, bars = days[di]
        state = new_state()            # the day reset, as in the script
        prev_close = days[di - 1][1][-1][4]
        for i, (ts, o, h, l, cl) in enumerate(bars):
            fresh = step(state, (o, h, l, cl), prev_close, levels)
            prev_close = cl
            for st in fresh:
                ev, direction = _EVENT_KEY[st]
                close_px, fwd, mfe, mae, nb = _grade_day(
                    bars, i, cl, direction)
                events.append((tk, d, state["lvl_nm"], state["lvl"], ev,
                               direction, ts, round(cl, 4),
                               round(close_px, 4), fwd, mfe, mae, nb))
    if events:
        with conn.cursor() as c:
            c.executemany("""INSERT INTO tapebot_retest_events
                (ticker, trade_date, level_name, level_px, event,
                 direction, signal_ts, entry_px, day_close_px,
                 fwd_close_bps, mfe_bps, mae_bps, bars_to_close)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""", events)
        conn.commit()
        inserted = len(events)
    log.info("[tapebot-retest] %s: %d days, %d signal(s).",
             tk, len(days), inserted)
    return inserted


def run() -> bool:
    """One-shot with marker retirement; idempotent on re-run."""
    from screen.reversal_screen import _conn
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
        total = 0
        for tk in TICKERS:
            if time.time() - t0 > BUDGET_S:
                log.info("[tapebot-retest] budget hit; resuming next boot.")
                return False
            total += _one_ticker(conn, tk)
        with conn.cursor() as c:
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                      "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                      (COMPLETE_MARKER,))
        conn.commit()
        log.info("[tapebot-retest] complete — %d events, marker %s.",
                 total, COMPLETE_MARKER)
        return True
    finally:
        conn.close()
