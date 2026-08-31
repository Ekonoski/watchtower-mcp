"""
Mag-7 1-minute history backfill for the RS-leader study (2026-09-01 —
Eric: "yes run the RS-leader study tonight"). Two years of RTH 1m bars
for the seven names into mag7_1m_bars, fetched from Polygon in
window-sized chunks (1m density: ~390 bars/day, so windows stay at 10
days to sit under the ~5,000-row response cap the day-bias backfill
discovered the hard way). Resumable by max stored date per ticker,
marker-retired. Research backfill — reconstruction-is-not-tape governs
live grading, not history studies. QQQ reference intentionally NOT
fetched at 1m: the 9:45 RS measurement aligns with the stored 15m
index_intraday_bars record.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.rsleader_bars")

COMPLETE_MARKER = "rsleader_bars_v1"
TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
START = dt.date(2024, 9, 1)
WINDOW_DAYS = 10
RESPONSE_CAP_WARN = 4500
BUDGET_S = 25 * 60
ET = "America/New_York"


def _rth_rows(aggs, ticker):
    from zoneinfo import ZoneInfo
    et = ZoneInfo(ET)
    rows = []
    for a in aggs:
        t = dt.datetime.fromtimestamp(a.timestamp / 1000,
                                      dt.timezone.utc).astimezone(et)
        if dt.time(9, 30) <= t.time() <= dt.time(15, 59):
            rows.append((ticker, t, t.date(), float(a.open), float(a.high),
                         float(a.low), float(a.close),
                         float(a.volume) if a.volume is not None else None))
    return rows


def run() -> bool:
    """One budgeted pass; True when all tickers reach the present."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn

    client = get_client()
    if client is None:
        log.warning("[rsleader-bars] no Polygon client — skipped.")
        return False
    conn = _conn()
    t0 = time.time()
    today = dt.date.today()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
        all_done = True
        for tk in TICKERS:
            with conn.cursor() as cur:
                cur.execute("SELECT max(trade_date) FROM mag7_1m_bars "
                            "WHERE ticker=%s", (tk,))
                r = cur.fetchone()
            cursor_date = (r[0] + dt.timedelta(days=1)) if r and r[0] else START
            while cursor_date <= today:
                if time.time() - t0 > BUDGET_S:
                    log.info("[rsleader-bars] budget hit; resuming next boot.")
                    return False
                w_end = min(cursor_date + dt.timedelta(days=WINDOW_DAYS - 1),
                            today)
                try:
                    aggs = list(client.get_aggs(
                        tk, multiplier=1, timespan="minute",
                        from_=cursor_date.isoformat(),
                        to=w_end.isoformat(), limit=50000))
                except Exception as e:
                    log.warning(f"[rsleader-bars] {tk} {cursor_date}..{w_end} "
                                f"fetch failed (retry next boot): {e}")
                    all_done = False
                    break
                if len(aggs) >= RESPONSE_CAP_WARN:
                    log.warning(f"[rsleader-bars] {tk} {cursor_date}..{w_end}: "
                                f"{len(aggs)} aggs — near the response cap; "
                                f"window may be TRUNCATED.")
                rows = _rth_rows(aggs, tk)
                if rows:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """INSERT INTO mag7_1m_bars
                               (ticker, ts, trade_date, open, high, low,
                                close, volume)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (ticker, ts) DO NOTHING""", rows)
                    conn.commit()
                else:
                    log.warning(f"[rsleader-bars] {tk} {cursor_date}..{w_end}: "
                                f"0 RTH bars — history hole.")
                cursor_date = w_end + dt.timedelta(days=1)
        if all_done:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,))
            conn.commit()
            log.info(f"[rsleader-bars] complete — marker {COMPLETE_MARKER}.")
            return True
        return False
    finally:
        conn.close()
