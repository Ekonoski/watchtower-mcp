"""
Daily appender for the research bar tables (2026-08-31, the frozen-
table find; widened 2026-09-03): the SPY/QQQ/IWM 15m record was only
ever written by the one-shot day-bias backfill, so it froze at
2026-08-21 the day that backfill completed — cutting the flip-proximity
study off exactly before the chop days it was built to grade. On
2026-09-03 the same disease surfaced in the 1m tables: mag7_1m_bars and
liquid_1m_bars were one-shot backfills through 2026-08-31, so Eric's
9/2 fills could not be read at their minute and every intraday study
was silently a study of the past. A research table nobody appends to
is a `_social_block` with a date on it.

This module owns the freshness of all three: resume from
max(trade_date) per ticker, fetch RTH aggregates, append. Runs 16:20 ET
weekdays plus a boot catch-up pass (cheap no-op when current). IWM's
vendor history is holey (recorded 2026-08-23) — appended anyway; holes
stay visible. Live books are unaffected either way: day_bias decides on
paper_spec_bars and the RS-leader book on rsl_book_bars, never on these
tables. Refetching here is legitimate — these are research records, and
reconstruction-is-not-tape governs LIVE grading only.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.index_bars")

ET = "America/New_York"
MAX_CATCHUP_DAYS = 40
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
LIQUID = ("AMD", "IWM", "QQQ", "SPY")
# (table, tickers, bar minutes, last RTH bar start)
TARGETS = (
    ("index_intraday_bars", ("SPY", "QQQ", "IWM"), 15, dt.time(15, 45)),
    ("mag7_1m_bars", MAG7, 1, dt.time(15, 59)),
    ("liquid_1m_bars", LIQUID, 1, dt.time(15, 59)),
)
TICKERS = TARGETS[0][1]          # kept for older callers/tests


def rth_rows(aggs, tk, et, last_start):
    """Pure. Polygon aggs -> (ticker, ts, date, o, h, l, c, v) rows for
    bars starting 09:30..last_start ET."""
    rows = []
    for a in aggs:
        t = dt.datetime.fromtimestamp(a.timestamp / 1000, dt.timezone.utc).astimezone(et)
        if dt.time(9, 30) <= t.time() <= last_start:
            rows.append((tk, t, t.date(), float(a.open), float(a.high), float(a.low),
                         float(a.close), float(a.volume) if a.volume is not None else None))
    return rows


def _append_table(conn, client, table, tickers, minutes, last_start, et, today):
    total = 0
    for tk in tickers:
        with conn.cursor() as c:
            c.execute(f"SELECT max(trade_date) FROM {table} WHERE ticker=%s", (tk,))
            r = c.fetchone()
        if not r or not r[0]:
            log.warning(f"[index-bars] {table}/{tk}: empty table — this is the "
                        f"appender, not the backfill; skipping.")
            continue
        start = r[0] + dt.timedelta(days=1)
        if start > today:
            continue
        if (today - start).days > MAX_CATCHUP_DAYS:
            start = today - dt.timedelta(days=MAX_CATCHUP_DAYS)
            log.warning(f"[index-bars] {table}/{tk}: gap exceeds {MAX_CATCHUP_DAYS}d — "
                        f"appending the recent window only; older gap stays a recorded hole.")
        try:
            # list_aggs paginates past Polygon's ~5k-row response cap (the
            # 2026-08-23 lesson); 40 days of 1m bars is ~15.6k rows.
            aggs = list(client.list_aggs(tk, multiplier=minutes, timespan="minute",
                                         from_=start.isoformat(), to=today.isoformat(),
                                         limit=50000))
        except Exception as e:
            log.warning(f"[index-bars] {table}/{tk} fetch failed: {e}")
            continue
        rows = rth_rows(aggs, tk, et, last_start)
        if rows:
            with conn.cursor() as c:
                c.executemany(
                    f"""INSERT INTO {table}
                        (ticker, ts, trade_date, open, high, low, close, volume)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (ticker, ts) DO NOTHING""", rows)
            conn.commit()
            total += len(rows)
            log.info(f"[index-bars] {table}/{tk}: +{len(rows)} bars through {rows[-1][2]}.")
        else:
            log.info(f"[index-bars] {table}/{tk}: nothing to append ({start}..{today}).")
    return total


def run() -> int:
    """Append missing days per table and ticker; returns rows written."""
    from zoneinfo import ZoneInfo

    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn

    client = get_client()
    if client is None:
        log.warning("[index-bars] no Polygon client — skipped.")
        return 0
    et = ZoneInfo(ET)
    today = dt.datetime.now(et).date()
    conn = _conn()
    total = 0
    try:
        for table, tickers, minutes, last_start in TARGETS:
            total += _append_table(conn, client, table, tickers, minutes, last_start, et, today)
        return total
    finally:
        conn.close()
