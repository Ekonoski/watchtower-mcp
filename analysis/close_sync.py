"""
Same-evening close sync — the day's daily bars from Polygon at the bell.

Eric, 2026-08-14: "our data is realtime. that shouldn't be an issue ever
again." The desk's daily/weekly oscillator reads were hostage to the
nightly FMP batch (price-cron, ~10 PM ET): all evening the freshest bar
in daily_prices was YESTERDAY'S, and Friday's close would not reach a
screen until Monday's 6:45 scan. But one grouped-daily call to Polygon
returns every US ticker's completed session bar minutes after the close.

This module upserts the day's bars for tickers the desk already tracks —
never inserting unknown symbols, so the table's universe stays owned by
the ingestion pipeline — and the 4:35 PM re-stamp then reads TODAY. The
nightly FMP ingest remains the settling authority: its later upsert
overwrites these rows with official closes.
"""
import datetime as dt
import logging
from zoneinfo import ZoneInfo

log = logging.getLogger("watchtower.close_sync")

ET = ZoneInfo("America/New_York")


def _sync_rows(aggs, known: set, day: dt.date) -> list:
    """Shape grouped-daily aggregates into daily_prices upsert tuples.
    Pure: drops unknown tickers (the table's universe is the ingestion
    pipeline's decision, not Polygon's) and bars missing a close."""
    out = []
    for a in aggs or []:
        tk = getattr(a, "ticker", None)
        close = getattr(a, "close", None)
        if not tk or tk not in known or close is None:
            continue
        out.append((tk, day,
                    getattr(a, "open", None), getattr(a, "high", None),
                    getattr(a, "low", None), float(close),
                    getattr(a, "volume", None)))
    return out


def sync_todays_closes(conn=None, day: dt.date = None) -> int:
    """Fetch the session's grouped daily bars and upsert them for known
    tickers. Returns rows written; 0 is an honest signal that nothing
    landed (no key, market holiday, Polygon gap) — callers must treat it
    as 'the table still shows yesterday', never as success."""
    from psycopg2.extras import execute_values
    from analysis.polygon_data import get_client

    client = get_client()
    if client is None:
        log.warning("[close-sync] no Polygon client — sync unavailable")
        return 0
    if day is None:
        day = dt.datetime.now(ET).date()
    try:
        aggs = client.get_grouped_daily_aggs(str(day), adjusted=True)
    except Exception as e:
        log.warning(f"[close-sync] grouped-daily fetch failed: {e}")
        return 0

    own_conn = conn is None
    if own_conn:
        from screen.reversal_screen import _conn
        conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM daily_prices "
                        "WHERE trade_date >= %s", (day - dt.timedelta(days=30),))
            known = {r[0] for r in cur.fetchall()}
        rows = _sync_rows(aggs, known, day)
        if not rows:
            log.warning(f"[close-sync] {day}: 0 known-ticker bars in "
                        f"{len(aggs or [])} grouped aggs — nothing written")
            return 0
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO daily_prices (ticker, trade_date, open, high, low,
                                          close, volume)
                VALUES %s
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    open = EXCLUDED.open, high = EXCLUDED.high,
                    low = EXCLUDED.low, close = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """, rows, page_size=1000)
        conn.commit()
        log.info(f"[close-sync] {day}: {len(rows)} tickers upserted "
                 f"(of {len(aggs or [])} on the tape)")
        return len(rows)
    finally:
        if own_conn:
            conn.close()
