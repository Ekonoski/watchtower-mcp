"""
The rank-ladder study (2026-09-01, pre-registered the afternoon MSFT
(#2, +1.29 RS) died while META (#1) ran +12R and AAPL (#3) ran too —
Eric: "MSFT was the second name on that list and it failed miserably"
/ "AAPL also ran hard, MSFT just died"). One day sorted nothing; two
years can.

QUESTION: does the graded GO entry (first 1m 8/21 hold, 9:45-11:00,
at its close) carry edge at every 9:45 rank position, only at rank 1,
only above the +0.4% RS bar, or only when the name's OWN structure
confirms?

Per (trade_date, ticker) across the mag-7 1m record: rank_pos (1-7 by
RS vs QQQ at 9:45), rs, qualified (rs >= RS_MIN), the GO entry if one
printed, and two structure states AT ENTRY (knowable then, never
later): orb_state (entry above/inside/below the 30-min opening range)
and trend5_on — REDEFINED 2026-09-01: the column now holds the 1m
trend gate (tapeentry's ema_1m_gated frame) at the GO bar, because
the original 5m gate needed 21 completed 5m bars (~11:15) while the
entry window ends at 11:00 — it could never fire (all 3,465 first-run
rows read False; wiped and re-graded). NULL = the GO printed inside
the 1m gate's own 21-bar warmup: unknown, never False. Outcomes: eod_bps (entry -> day close, no
stop) and r_close (per struct risk; cap at readout — outlier rule).

Readout bars, pre-registered: a rank cohort or structure cell is real
only if positive in BOTH year-halves AND sign-consistent in >=5 of 7
names. Caveats where numbers surface: closes as fills, no costs,
2-year record, QQQ ref from the 15m index record.

Writes ONLY rankladder_events; resumes by (date, ticker); marker
rankladder_v1 when the bars marker exists and nothing is ungraded.
"""
import datetime as dt
import logging
import time

from analysis.rsleader_study import (ENTRY_CUTOFF, MEASURE, RS_MIN, TICKERS,
                                     ema, find_go_entry)
from analysis.tapeentry_study import trend_series

log = logging.getLogger("watchtower.rankladder")

COMPLETE_MARKER = "rankladder_v1"
BARS_MARKER = "rsleader_bars_v1"
BUDGET_S = 15 * 60
ORB_END = dt.time(10, 0)


def trend1m_at(closes, e8, e21, i):
    """The trend gate the entry window can actually KNOW (2026-09-01
    rework): the original 5m gate needed 21 completed 5m bars (~11:15)
    while entries end at 11:00, so trend5_on could never fire — a hole
    wearing False, the wma_touch family. This is the 1m trend
    (tapeentry's ema_1m_gated frame) at the GO bar; inside its own
    21-bar warmup the answer is None — unknown is not False."""
    if i < 21:
        return None
    return trend_series(closes, e8, e21)[i] == 1


def _grade_day(conn, d, done_pairs):
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    with conn.cursor() as c:
        c.execute("""SELECT open, close FROM index_intraday_bars
                     WHERE ticker='QQQ' AND trade_date=%s
                       AND (ts AT TIME ZONE 'America/New_York')::time='09:30'
                  """, (d,))
        r = c.fetchone()
    if not r:
        return 0
    qqq_ret = (float(r[1]) / float(r[0]) - 1) * 100
    day = {}
    with conn.cursor() as c:
        c.execute("""SELECT ticker, ts, open, high, low, close
                     FROM mag7_1m_bars WHERE trade_date=%s
                     ORDER BY ticker, ts""", (d,))
        for tk, ts, o, h, l, cl in c.fetchall():
            day.setdefault(tk, []).append(
                (ts.astimezone(et), float(o), float(h), float(l), float(cl)))
    rets = {}
    for tk, bars in day.items():
        if len(bars) < 300:
            continue
        px = None
        for b in bars:
            if b[0].time() < MEASURE:
                px = b[4]
            else:
                break
        if px is not None:
            rets[tk] = (px / bars[0][1] - 1) * 100
    if len(rets) < 7:
        return 0
    rs = {t: rets[t] - qqq_ret for t in rets}
    ordered = sorted(rs, key=lambda t: rs[t], reverse=True)

    rows = []
    for pos, tk in enumerate(ordered, start=1):
        if (d, tk) in done_pairs:
            continue
        bars = day[tk]
        closes = [b[4] for b in bars]
        e8, e21 = ema(closes, 8), ema(closes, 21)
        i945 = next((i for i, b in enumerate(bars)
                     if b[0].time() >= MEASURE), None)
        icut = next((i for i, b in enumerate(bars)
                     if b[0].time() >= ENTRY_CUTOFF), len(bars))
        if i945 is None:
            continue
        got = find_go_entry(bars, e8, e21, i945, icut, "long")
        if got is None:
            rows.append((d, tk, pos, round(rs[tk], 2), rs[tk] >= RS_MIN,
                         None, None, None, None, None, None, None))
            continue
        i, entry, stop = got
        orb_h = max(b[2] for b in bars if b[0].time() < ORB_END)
        orb_l = min(b[3] for b in bars if b[0].time() < ORB_END)
        orb_state = ("above" if entry > orb_h else
                     "below" if entry < orb_l else "inside")
        trend_on = trend1m_at(closes, e8, e21, i)
        eod_bps = (bars[-1][4] / entry - 1) * 1e4
        risk = entry - stop
        r_close = (bars[-1][4] - entry) / risk if risk > 0 else None
        rows.append((d, tk, pos, round(rs[tk], 2), rs[tk] >= RS_MIN,
                     bars[i][0], round(entry, 4), round(stop, 4),
                     orb_state, trend_on, round(eod_bps, 1),
                     round(r_close, 2) if r_close is not None else None))
    if rows:
        with conn.cursor() as c:
            c.executemany("""INSERT INTO rankladder_events
                (trade_date, ticker, rank_pos, rs_945, qualified,
                 entry_ts, entry_px, stop_px, orb_state, trend5_on,
                 eod_bps, r_close)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""", rows)
        conn.commit()
    return len(rows)


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
                      (BARS_MARKER,))
            bars_done = c.fetchone() is not None
            c.execute("SELECT trade_date, ticker FROM rankladder_events")
            done_pairs = set(c.fetchall())
            c.execute("""SELECT DISTINCT b.trade_date FROM mag7_1m_bars b
                         WHERE NOT EXISTS (SELECT 1 FROM rankladder_events e
                            WHERE e.trade_date=b.trade_date
                              AND e.ticker=b.ticker)
                         ORDER BY b.trade_date""")
            todo = [r[0] for r in c.fetchall()]
        n = 0
        for d in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[rankladder] budget hit; resuming.")
                return False
            n += _grade_day(conn, d, done_pairs)
        log.info("[rankladder] graded %d rows across %d day(s).", n, len(todo))
        if bars_done:
            # Every remaining day was attempted against the FINAL bar
            # record (bars marker present): a day that still yields no
            # rows — half-days, <300-bar sessions — is a permanently
            # ungradeable hole, not pending work. Without this the
            # marker could never write (2026-09-01: five half-days
            # held it open forever — a completion test no run could
            # ever pass, the class-that-can-never-fire disease).
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,))
            conn.commit()
            return True
        return not todo
    finally:
        conn.close()
