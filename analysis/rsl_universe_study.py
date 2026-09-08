"""
The RS-leader UNIVERSE study (2026-09-08 — Eric: "tell me which names
are the best for what I do and then run it through to see if they are
all good fits for the morning leader board").

The 🏁/🎯 leader board is the mag-7 BY GRADE, not by liquidity: the
RS-leader study (rsleader_study.py) replicated in all seven names, and
that replication is the reason the alert exists. Five liquid, high-beta,
QQQ-correlated candidates are graded here for a seat — AMD, AVGO, PLTR,
MU, NFLX — with the SAME definitions imported from the graded study
(rs_rank, find_go_entry, sim_bracket, ema, RS_MIN, MEASURE,
ENTRY_CUTOFF): leader = rank 1 by return-from-open minus QQQ's at 9:45
with RS >= +0.4%; entry = the first 1m 8/21 pullback that CLOSES holding
(wick rule) between 9:45 and 11:00; outcomes = the study's bracket sim
plus R at the close. Nothing is reimplemented.

Universes graded per day, each with its own leader / laggard / midpack
rows:
  mag7        the incumbents alone — the CONTROL; must reproduce
              rs_leader_events' leader rows (a mismatch is a bug, not a
              finding)
  mag7+X      the incumbents plus ONE candidate — each candidate's own
              leader sample, so a name's n does not depend on how often
              it out-runs TSLA in a crowd
  mag12       all twelve — the board as it would actually be, where the
              question is DISPLACEMENT: on days the twelve-name leader
              differs from the seven-name leader, which one paid?

PRE-REGISTERED BAR (frozen before any number): a candidate X earns a
seat only if, as leader in mag7+X, its GO expectancy (r_close, capped
+-10R, closes as fills, no costs) is POSITIVE IN BOTH YEAR-HALVES
(2024-09..2025-08 / 2025-09..) with n >= 15 leader GOs per half, AND
mag7+X's leader-day expectancy is >= mag7's in BOTH halves (a seat must
not dilute the board). mag12 reads out the displacement cost as a
second check. Cells under n=15 render small-n; a name that never leads
is a recorded non-fit, not a hole. Entries at closes, bracket sim as an
order sim, 2-year 1m record — stated wherever the numbers surface.

Bars: the mag-7 from mag7_1m_bars; the candidates from liquid_1m_bars
(backfilled by liquid_bars.run_v2, appended daily by index_bars_daily).
Index ETFs in liquid_1m_bars (SPY/QQQ/IWM) are NEVER ranked — the
universe lists are explicit. Writes ONLY rsl_universe_events; resumes
by graded trade_date; marker rsl_universe_v1 once the candidate bars
are complete and every stored day is graded.
"""
import datetime as dt
import logging
import time

from analysis.rsleader_study import (ENTRY_CUTOFF, MEASURE, STOP_BUFF, TICKERS as MAG7,
                                     ema, find_go_entry, rs_rank, sim_bracket)

log = logging.getLogger("watchtower.rsl_universe")

COMPLETE_MARKER = "rsl_universe_v1"
BARS_MARKER = "liquid_bars_v2"
CANDIDATES = ("AMD", "AVGO", "PLTR", "MU", "NFLX")
HALF_SPLIT = dt.date(2025, 9, 1)
MIN_N_HALF = 15
BUDGET_S = 20 * 60


def universes():
    """Pure: {name: tuple(tickers)} — the control, one per candidate, all."""
    u = {"mag7": tuple(MAG7)}
    for x in CANDIDATES:
        u[f"mag7+{x}"] = tuple(MAG7) + (x,)
    u["mag12"] = tuple(MAG7) + tuple(CANDIDATES)
    return u


def rets_at_945(day):
    """Pure: {ticker: bars} -> {ticker: pct return open→last close before 9:45}
    for tickers with a full-enough day (>= 120 bars)."""
    rets = {}
    for tk, bars in day.items():
        if len(bars) < 120:
            continue
        o930 = bars[0][1]
        p945 = None
        for ts, o, h, l, cl in bars:
            if ts.time() < MEASURE:
                p945 = cl
            else:
                break
        if p945 is not None:
            rets[tk] = (p945 / o930 - 1) * 100
    return rets


def grade_universe(name, members, day, qqq_ret, d):
    """Pure: the study's _grade_day logic for one universe. Returns rows
    shaped like rs_leader_events plus the universe name in front."""
    rets = {tk: r for tk, r in rets_at_945({t: day[t] for t in members if t in day}).items()}
    if len(rets) < 5:
        return []
    leader, laggard, midpack, rs = rs_rank(rets, qqq_ret)
    rows = []
    for tk, role, direction in ((leader, "leader", "long"),
                                (laggard, "laggard", "short"),
                                (midpack, "midpack_baseline", "long")):
        if tk is None:
            continue
        bars = day[tk]
        closes = [b[4] for b in bars]
        e8, e21 = ema(closes, 8), ema(closes, 21)
        i945 = next((i for i, b in enumerate(bars) if b[0].time() >= MEASURE), None)
        icut = next((i for i, b in enumerate(bars) if b[0].time() >= ENTRY_CUTOFF), len(bars))
        if i945 is None:
            continue
        got = find_go_entry(bars, e8, e21, i945, icut, direction)
        if got is None:
            rows.append((name, d, tk, role, direction, round(rs[tk], 2), "go_pullback",
                         None, None, None, "no_entry", None, None, None, None, None))
        else:
            i, entry, stop = got
            out = sim_bracket(bars, i, entry, stop, direction)
            rows.append((name, d, tk, role, direction, round(rs[tk], 2), "go_pullback",
                         bars[i][0], round(entry, 4), round(stop, 4), *out))
        if role == "leader":
            i = i945
            entry = bars[i][4]
            stop = min(b[3] for b in bars[:i + 1]) * (1 - STOP_BUFF)
            out = sim_bracket(bars, i, entry, stop, "long")
            rows.append((name, d, tk, role, "long", round(rs[tk], 2), "no_pullback_945",
                         bars[i][0], round(entry, 4), round(stop, 4), *out))
    return rows


def _load_day(conn, d, et):
    day = {}
    with conn.cursor() as c:
        c.execute("""SELECT ticker, ts, open, high, low, close FROM mag7_1m_bars
                     WHERE trade_date=%s ORDER BY ticker, ts""", (d,))
        for tk, ts, o, h, l, cl in c.fetchall():
            day.setdefault(tk, []).append((ts.astimezone(et), float(o), float(h), float(l), float(cl)))
        c.execute("""SELECT ticker, ts, open, high, low, close FROM liquid_1m_bars
                     WHERE trade_date=%s AND ticker = ANY(%s) ORDER BY ticker, ts""",
                  (d, list(CANDIDATES)))
        for tk, ts, o, h, l, cl in c.fetchall():
            day.setdefault(tk, []).append((ts.astimezone(et), float(o), float(h), float(l), float(cl)))
        c.execute("""SELECT open, close FROM index_intraday_bars
                     WHERE ticker='QQQ' AND trade_date=%s
                       AND (ts AT TIME ZONE 'America/New_York')::time='09:30'""", (d,))
        r = c.fetchone()
    qqq_ret = (float(r[1]) / float(r[0]) - 1) * 100 if r else None
    return day, qqq_ret


def _grade_day(conn, d, et):
    day, qqq_ret = _load_day(conn, d, et)
    if qqq_ret is None:
        return 0
    missing = [x for x in CANDIDATES if x not in day]
    if missing:
        log.info("[rsl-universe] %s: candidate bars missing for %s — day skipped (hole).", d, missing)
        return 0
    rows = []
    for name, members in universes().items():
        rows.extend(grade_universe(name, members, day, qqq_ret, d))
    if rows:
        with conn.cursor() as c:
            c.executemany("""INSERT INTO rsl_universe_events
                (universe, trade_date, ticker, role, direction, rs_945_pct, entry_kind,
                 entry_ts, entry_px, stop_px, outcome, r_first, r_noon, r_close, mfe_r, mae_r)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""", rows)
        conn.commit()
    return len(rows)


def run() -> bool:
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (BARS_MARKER,))
            bars_done = c.fetchone() is not None
            if not bars_done:
                log.info("[rsl-universe] candidate bars not complete yet — waiting.")
                return False
            c.execute("""SELECT DISTINCT b.trade_date FROM mag7_1m_bars b
                         WHERE NOT EXISTS (SELECT 1 FROM rsl_universe_events e
                                           WHERE e.trade_date = b.trade_date)
                         ORDER BY b.trade_date""")
            todo = [r[0] for r in c.fetchall()]
        n = 0
        for d in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[rsl-universe] budget hit at %s; resuming.", d)
                return False
            n += _grade_day(conn, d, et)
        log.info("[rsl-universe] graded %d rows across %d day(s).", n, len(todo))
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                          "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
            conn.commit()
            log.info("[rsl-universe] complete — marker %s.", COMPLETE_MARKER)
            log.info("[rsl-universe] readout:\n%s", READOUT_SQL)
            return True
        return False
    finally:
        conn.close()


READOUT_SQL = """
-- per universe x half: leader GO count, capped avg R to close, win rate
SELECT universe, CASE WHEN trade_date < '2025-09-01' THEN 'h1' ELSE 'h2' END AS half,
       count(*) AS n, round(avg(LEAST(GREATEST(r_close,-10),10))::numeric,2) AS avg_r,
       round(100.0*avg((r_close>0)::int),0) AS win_pct
FROM rsl_universe_events
WHERE role='leader' AND entry_kind='go_pullback' AND outcome<>'no_entry' AND r_close IS NOT NULL
GROUP BY 1,2 ORDER BY 1,2;
-- each candidate AS LEADER in its own mag7+X universe (the seat test)
SELECT universe, ticker, CASE WHEN trade_date < '2025-09-01' THEN 'h1' ELSE 'h2' END AS half,
       count(*) AS n, round(avg(LEAST(GREATEST(r_close,-10),10))::numeric,2) AS avg_r,
       round(100.0*avg((r_close>0)::int),0) AS win_pct
FROM rsl_universe_events
WHERE role='leader' AND entry_kind='go_pullback' AND outcome<>'no_entry' AND r_close IS NOT NULL
  AND universe LIKE 'mag7+%' AND ticker = substr(universe, 6)
GROUP BY 1,2,3 ORDER BY 1,3;
-- displacement in mag12: the twelve-name leader vs the seven-name leader on the same day
SELECT CASE WHEN a.trade_date < '2025-09-01' THEN 'h1' ELSE 'h2' END AS half,
       count(*) AS displaced_days,
       round(avg(LEAST(GREATEST(a.r_close,-10),10))::numeric,2) AS mag12_leader_r,
       round(avg(LEAST(GREATEST(b.r_close,-10),10))::numeric,2) AS mag7_leader_r
FROM rsl_universe_events a JOIN rsl_universe_events b
  ON b.trade_date=a.trade_date AND b.universe='mag7' AND b.role='leader' AND b.entry_kind='go_pullback'
WHERE a.universe='mag12' AND a.role='leader' AND a.entry_kind='go_pullback' AND a.ticker<>b.ticker
  AND a.r_close IS NOT NULL AND b.r_close IS NOT NULL
GROUP BY 1 ORDER BY 1;
"""
