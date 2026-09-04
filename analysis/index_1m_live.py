"""
Per-minute persistence of the index tape during the session (2026-09-04,
Eric at 9:31 asking for the 9:30 open: the mega-caps had a live
1m record via the RS-leader book, the indexes did not — only 15m bars,
written at the books' 15m cadence, and the 1m tables appended at 16:20).

Every minute 9:31-16:01 ET: fetch today's 1m aggregates for LIVE_NAMES
from the real-time feed and insert the COMPLETED regular-session bars
into liquid_1m_bars, ON CONFLICT DO NOTHING — written as first seen,
never revised (the rsl_book_bars rule). The 16:20 appender still runs
and simply finds nothing to add. No book reads this table to decide;
it exists so the record can answer "what printed at 9:30?" at 9:31.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.index_1m_live")

LIVE_NAMES = ("SPY", "QQQ", "IWM", "AMD")
TABLE = "liquid_1m_bars"
RTH_START, RTH_END = dt.time(9, 30), dt.time(15, 59)


def completed_rth_rows(aggs, tk, et, cutoff):
    """Pure. Keep 09:30..15:59 bars whose start is before `cutoff` (the
    current minute, floored) — the forming bar never lands."""
    rows = []
    for a in aggs:
        t = dt.datetime.fromtimestamp(a.timestamp / 1000, dt.timezone.utc).astimezone(et)
        if RTH_START <= t.time() <= RTH_END and t < cutoff:
            rows.append((tk, t, t.date(), float(a.open), float(a.high), float(a.low),
                         float(a.close), float(a.volume) if a.volume is not None else None))
    return rows


def run_tick() -> int:
    from zoneinfo import ZoneInfo

    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    now = dt.datetime.now(et)
    if now.weekday() >= 5 or not (dt.time(9, 31) <= now.time() <= dt.time(16, 5)):
        return 0
    client = get_client()
    if client is None:
        return 0
    cutoff = now.replace(second=0, microsecond=0)
    today = now.date()
    conn = _conn()
    total = 0
    try:
        for tk in LIVE_NAMES:
            try:
                aggs = list(client.get_aggs(tk, multiplier=1, timespan="minute",
                                            from_=today.isoformat(), to=today.isoformat(),
                                            limit=1200))
            except Exception as e:
                log.warning(f"[index-1m] {tk} fetch failed: {e}")
                continue
            rows = completed_rth_rows(aggs, tk, et, cutoff)
            if not rows:
                continue
            with conn.cursor() as c:
                c.executemany(
                    f"""INSERT INTO {TABLE}
                        (ticker, ts, trade_date, open, high, low, close, volume)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (ticker, ts) DO NOTHING""", rows)
                total += c.rowcount if c.rowcount and c.rowcount > 0 else 0
            conn.commit()
        return total
    finally:
        conn.close()
