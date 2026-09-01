"""
The HOD/LOD time map (2026-09-01, from the pass-2 indicator scan —
P2-19, the cheapest priority item): across 20 years of stored 15m
index bars, WHICH time bucket prints the day's high and low, and how
does that shift with the day-bias state? Bears directly on live
conventions: the bell exit (is the close usually the high on trend
days?) and the trail-after-1R (how often does the day's high print
after 11:00 on open-above days?).

Per (SPY/QQQ, day) with >=20 RTH bars: hi_time / lo_time (the 15m
bucket's start, ET), open_state vs the prior day's range
(open_above / open_below / inside — the day-bias vocabulary), and
close_pos (close's position in the day range, 0-1). Era split and
conditioning happen at readout in SQL; this table only records.

Writes ONLY hodlod_days; resumes by (ticker, day); marker hodlod_v1.
Caveats at readout: 15m granularity, SPY 2005->, QQQ 2011->.
"""
import logging
import time

log = logging.getLogger("watchtower.hodlod")

COMPLETE_MARKER = "hodlod_v1"
TICKERS = ("SPY", "QQQ")
BUDGET_S = 12 * 60


def day_row(bars, pdh, pdl):
    """Pure. bars: [(ts_et, o, h, l, c)] one day's RTH 15m, oldest
    first. Returns (hi_time, lo_time, open_state, close_pos)."""
    hi = max(bars, key=lambda b: b[2])
    lo = min(bars, key=lambda b: b[3])
    o = bars[0][1]
    open_state = ("open_above" if pdh is not None and o > pdh else
                  "open_below" if pdl is not None and o < pdl else "inside")
    day_hi, day_lo, c = hi[2], lo[3], bars[-1][4]
    close_pos = ((c - day_lo) / (day_hi - day_lo)
                 if day_hi > day_lo else None)
    return (hi[0].time().strftime("%H:%M"), lo[0].time().strftime("%H:%M"),
            open_state,
            round(close_pos, 3) if close_pos is not None else None)


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
        for tk in TICKERS:
            with conn.cursor() as c:
                c.execute("""SELECT DISTINCT b.trade_date
                             FROM index_intraday_bars b
                             WHERE b.ticker=%s AND NOT EXISTS
                               (SELECT 1 FROM hodlod_days h
                                WHERE h.ticker=%s AND h.trade_date=b.trade_date)
                             ORDER BY b.trade_date""", (tk, tk))
                days = [r[0] for r in c.fetchall()]
            prev_hi = prev_lo = None
            prev_d = None
            for d in days:
                if time.time() - t0 > BUDGET_S:
                    log.info("[hodlod] budget hit; resuming.")
                    return False
                with conn.cursor() as c:
                    c.execute("""SELECT ts, open, high, low, close
                                 FROM index_intraday_bars
                                 WHERE ticker=%s AND trade_date=%s
                                 ORDER BY ts""", (tk, d))
                    bars = [(ts.astimezone(et), float(o), float(h), float(l),
                             float(cl)) for ts, o, h, l, cl in c.fetchall()]
                    # prior-day range from the same record (contiguous
                    # resume means prev_* may be unset after a restart)
                    if prev_d is None:
                        c.execute("""SELECT max(high), min(low)
                                     FROM index_intraday_bars
                                     WHERE ticker=%s AND trade_date =
                                       (SELECT max(trade_date)
                                        FROM index_intraday_bars
                                        WHERE ticker=%s AND trade_date<%s)""",
                                  (tk, tk, d))
                        r = c.fetchone()
                        prev_hi = float(r[0]) if r and r[0] else None
                        prev_lo = float(r[1]) if r and r[1] else None
                if len(bars) >= 20:
                    hi_t, lo_t, open_state, close_pos = day_row(
                        bars, prev_hi, prev_lo)
                    with conn.cursor() as c:
                        c.execute("""INSERT INTO hodlod_days
                            (ticker, trade_date, hi_time, lo_time,
                             open_state, close_pos)
                            VALUES (%s,%s,%s,%s,%s,%s)
                            ON CONFLICT DO NOTHING""",
                            (tk, d, hi_t, lo_t, open_state, close_pos))
                    conn.commit()
                if bars:
                    prev_hi = max(b[2] for b in bars)
                    prev_lo = min(b[3] for b in bars)
                    prev_d = d
        with conn.cursor() as c:
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                      "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                      (COMPLETE_MARKER,))
        conn.commit()
        log.info("[hodlod] complete.")
        return True
    finally:
        conn.close()
