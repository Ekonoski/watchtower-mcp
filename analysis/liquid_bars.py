"""
1-minute history backfill for the tape-entry study's non-mag-7 names
(2026-09-02 — Eric: "find the most liquid names for options and test
them"). SPY/QQQ/IWM/AMD into liquid_1m_bars; the mag-7 already live in
mag7_1m_bars via the RS-leader backfill and are NOT duplicated here —
the two tables are deliberately separate because rsleader_study ranks
whatever tickers its table returns, and adding index ETFs there would
silently corrupt the RS rank. Same window sizing as rsleader_bars
(10-day windows under the ~5,000-row Polygon response cap), resumable
by max stored date per ticker, marker-retired. Research backfill.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.liquid_bars")

COMPLETE_MARKER = "liquid_bars_v1"
TICKERS = ("SPY", "QQQ", "IWM", "AMD")
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
        log.warning("[liquid-bars] no Polygon client — skipped.")
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
                cur.execute("SELECT max(trade_date) FROM liquid_1m_bars "
                            "WHERE ticker=%s", (tk,))
                r = cur.fetchone()
            cursor_date = (r[0] + dt.timedelta(days=1)) if r and r[0] else START
            while cursor_date <= today:
                if time.time() - t0 > BUDGET_S:
                    log.info("[liquid-bars] budget hit; resuming next boot.")
                    return False
                w_end = min(cursor_date + dt.timedelta(days=WINDOW_DAYS - 1),
                            today)
                try:
                    aggs = list(client.get_aggs(
                        tk, multiplier=1, timespan="minute",
                        from_=cursor_date.isoformat(),
                        to=w_end.isoformat(), limit=50000))
                except Exception as e:
                    log.warning(f"[liquid-bars] {tk} {cursor_date}..{w_end} "
                                f"fetch failed (retry next boot): {e}")
                    all_done = False
                    break
                if len(aggs) >= RESPONSE_CAP_WARN:
                    log.warning(f"[liquid-bars] {tk} {cursor_date}..{w_end}: "
                                f"{len(aggs)} aggs — near the response cap; "
                                f"window may be TRUNCATED.")
                rows = _rth_rows(aggs, tk)
                if rows:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """INSERT INTO liquid_1m_bars
                               (ticker, ts, trade_date, open, high, low,
                                close, volume)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (ticker, ts) DO NOTHING""", rows)
                    conn.commit()
                else:
                    log.warning(f"[liquid-bars] {tk} {cursor_date}..{w_end}: "
                                f"0 RTH bars — history hole.")
                cursor_date = w_end + dt.timedelta(days=1)
        if all_done:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,))
            conn.commit()
            log.info(f"[liquid-bars] complete — marker {COMPLETE_MARKER}.")
            return True
        return False
    finally:
        conn.close()
