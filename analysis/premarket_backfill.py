"""
Premarket range backfill (2026-09-03 — the exit-shape study needs the
premarket high as a take-profit level and the desk's 1m record is
regular-session only). A RESEARCH backfill from Polygon — legitimate
under the house rule (reconstruction-is-not-tape governs LIVE grading;
this feeds a study) — into its own table, never the live bar tables.

Per ticker (the 11 liquid names) and 10-calendar-day window, fetch 1m
aggregates (extended hours included), keep 04:00-09:29 ET, and write one
row per trade date: pm_high, pm_low, pm_bars. A day with no premarket
bars records pm_bars=0 (a quiet read); a window that failed to fetch is
NOT claimed and retries next pass (a hole, never a zero). Marker
premarket_backfill_v1 when every window is claimed.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.premarket")

COMPLETE_MARKER = "premarket_backfill_v1"
TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "IWM", "QQQ", "SPY")
START = dt.date(2024, 8, 20)
WINDOW_DAYS = 10
BUDGET_S = 12 * 60
PM_START, PM_END = dt.time(4, 0), dt.time(9, 29)


def premarket_ranges(rows, et):
    """Pure. rows: iterable of (epoch_ms, high, low). Returns
    {date: (pm_high, pm_low, n_bars)} for bars inside 04:00-09:29 ET."""
    out = {}
    for ms, h, l in rows:
        t = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).astimezone(et)
        if PM_START <= t.time() <= PM_END:
            d = t.date()
            cur = out.get(d)
            out[d] = ((h, l, 1) if cur is None else
                      (max(cur[0], h), min(cur[1], l), cur[2] + 1))
    return out


def _windows(end):
    d = START
    while d <= end:
        yield d, min(d + dt.timedelta(days=WINDOW_DAYS - 1), end)
        d += dt.timedelta(days=WINDOW_DAYS)


def run() -> bool:
    from zoneinfo import ZoneInfo
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("SELECT job_name FROM scheduler_job_claims WHERE job_name LIKE 'premarket_w_%%'")
            claimed = {r[0] for r in c.fetchall()}
        client = get_client()
        if client is None:
            log.warning("[premarket] no Polygon client; backfill skipped (hole).")
            return False
        end = dt.date.today() - dt.timedelta(days=1)
        todo = [(tk, a, b) for tk in TICKERS for a, b in _windows(end)
                if f"premarket_w_{tk}_{a.isoformat()}" not in claimed]
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                          "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
            conn.commit()
            return True
        n = 0
        for tk, a, b in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[premarket] budget hit; %d windows this pass, resuming.", n)
                return False
            try:
                aggs = client.list_aggs(tk, multiplier=1, timespan="minute",
                                        from_=a.isoformat(), to=b.isoformat(), limit=50000)
                rows = [(x.timestamp, float(x.high), float(x.low)) for x in aggs]
            except Exception as e:
                log.warning(f"[premarket] {tk} {a}..{b} fetch failed: {e}")
                continue
            ranges = premarket_ranges(rows, et)
            with conn.cursor() as c:
                d = a
                while d <= b:
                    if d.weekday() < 5:
                        r = ranges.get(d)
                        c.execute("""INSERT INTO premarket_range (ticker, trade_date, pm_high, pm_low, pm_bars)
                                     VALUES (%s,%s,%s,%s,%s)
                                     ON CONFLICT (ticker, trade_date) DO UPDATE SET
                                       pm_high=EXCLUDED.pm_high, pm_low=EXCLUDED.pm_low,
                                       pm_bars=EXCLUDED.pm_bars""",
                                  (tk, d, r[0] if r else None, r[1] if r else None, r[2] if r else 0))
                    d += dt.timedelta(days=1)
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                          "ON CONFLICT DO NOTHING", (f"premarket_w_{tk}_{a.isoformat()}",))
            conn.commit()
            n += 1
        log.info("[premarket] %d windows this pass.", n)
        return False
    finally:
        conn.close()
