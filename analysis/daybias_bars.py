"""
Index intraday history backfill for the day-bias study (2026-08-23 —
Eric: "we have twenty years of data... figure out the best possible
way to figure out the daily bias... and then the best entries").

Phase 1 (daily bars, already read out): the open's location vs the
prior day's range and the prior close's strength carry large, era-
stable conditioning on PDH/PDL touch rates and close direction. What
daily bars CANNOT answer is sequencing — which level got touched
first, when the retest of a broken PDH arrives, what an entry there
earns vs bleeds intraday. That needs the intraday tape.

This module persists SPY/QQQ/IWM 15-minute RTH bars (2005→present,
~140k bars per ticker) into index_intraday_bars, fetched from Polygon
in year-sized windows, resumable by max-stored-date across boots,
marker-retired when all tickers reach the present. Research backfill —
the reconstruction-is-not-tape rule governs live grading, not history
studies — and once stored, every entry-model question is a SQL query
over our own recorded table, repeatable and free.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.daybias_bars")

COMPLETE_MARKER = "daybias_bars_v1"
TICKERS = ("SPY", "QQQ", "IWM")
START = dt.date(2005, 1, 1)
WINDOW_DAYS = 365          # one get_aggs call per ~year (26 RTH bars/day)
BUDGET_S = 30 * 60
ET = "America/New_York"


def _rth_rows(aggs, ticker):
    """Polygon aggs -> insert rows, RTH bar-starts only (9:30-15:45 ET)."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo(ET)
    rows = []
    for a in aggs:
        t = dt.datetime.fromtimestamp(a.timestamp / 1000,
                                      dt.timezone.utc).astimezone(et)
        if dt.time(9, 30) <= t.time() <= dt.time(15, 45):
            rows.append((ticker, t, t.date(), float(a.open), float(a.high),
                         float(a.low), float(a.close),
                         float(a.volume) if a.volume is not None else None))
    return rows


def run() -> bool:
    """One budgeted pass; True when every ticker's bars reach the
    present (marker written). Resumes from max stored date per ticker."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn

    client = get_client()
    if client is None:
        log.warning("[daybias-bars] no Polygon client — skipped.")
        return False
    conn = _conn()
    t0 = time.time()
    today = dt.date.today()
    try:
        all_done = True
        for tk in TICKERS:
            with conn.cursor() as cur:
                cur.execute("SELECT max(trade_date) FROM index_intraday_bars "
                            "WHERE ticker=%s", (tk,))
                r = cur.fetchone()
            cursor_date = (r[0] + dt.timedelta(days=1)) if r and r[0] else START
            while cursor_date <= today:
                if time.time() - t0 > BUDGET_S:
                    log.info("[daybias-bars] budget hit; resuming next boot.")
                    return False
                w_end = min(cursor_date + dt.timedelta(days=WINDOW_DAYS - 1),
                            today)
                try:
                    aggs = list(client.get_aggs(
                        tk, multiplier=15, timespan="minute",
                        from_=cursor_date.isoformat(),
                        to=w_end.isoformat(), limit=50000))
                except Exception as e:
                    log.warning(f"[daybias-bars] {tk} {cursor_date}..{w_end} "
                                f"fetch failed (will retry next boot): {e}")
                    all_done = False
                    break
                rows = _rth_rows(aggs, tk)
                if rows:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """INSERT INTO index_intraday_bars
                               (ticker, ts, trade_date, open, high, low,
                                close, volume)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (ticker, ts) DO NOTHING""", rows)
                    conn.commit()
                else:
                    # A whole empty year is a recorded hole, not silence.
                    log.warning(f"[daybias-bars] {tk} {cursor_date}..{w_end}: "
                                f"0 RTH bars returned — history hole.")
                log.info(f"[daybias-bars] {tk} through {w_end}: "
                         f"+{len(rows)} bars")
                cursor_date = w_end + dt.timedelta(days=1)
        if all_done:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,))
            conn.commit()
            log.info(f"[daybias-bars] complete — marker {COMPLETE_MARKER}.")
            return True
        return False
    finally:
        conn.close()
