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
SESSION_COMPLETE_AT = dt.time(16, 5)   # a day is fetchable only after this ET
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
LIQUID = ("AMD", "IWM", "QQQ", "SPY", "AVGO", "PLTR", "MU", "NFLX")   # v2 names: the leader-board seat test (2026-09-08)
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


def append_window(last_recorded: dt.date, now_et: dt.datetime):
    """Pure. The (start, to) dates one append pass may fetch, or None.

    2026-09-15: the 11:07 boot catch-up on 9/14 ran mid-session, fetched the
    day so far (15m bars to 11:00, 1m bars to 11:08), wrote them, and — because
    the next pass resumes from max(trade_date)+1 — the partial day was CLAIMED
    complete; the 16:20 pass then had nothing to append. Two rules fix both
    halves: (1) TODAY is fetchable only once the session is complete
    (SESSION_COMPLETE_AT ET); before that the window ends yesterday, so a boot
    at any hour can never write a forming day; (2) the window starts AT the
    last recorded day, not after it — the insert is idempotent on (ticker,
    ts), so re-fetching one day costs nothing and back-fills any bars a
    partial write left out. The 40-day catch-up cap is unchanged; a wider gap
    stays a recorded hole."""
    today = now_et.date()
    to = today if now_et.time() >= SESSION_COMPLETE_AT else today - dt.timedelta(days=1)
    start = last_recorded
    if start > to:
        return None
    if (to - start).days > MAX_CATCHUP_DAYS:
        start = to - dt.timedelta(days=MAX_CATCHUP_DAYS)
    return start, to


def _append_table(conn, client, table, tickers, minutes, last_start, et, now_et):
    total = 0
    for tk in tickers:
        with conn.cursor() as c:
            c.execute(f"SELECT max(trade_date) FROM {table} WHERE ticker=%s", (tk,))
            r = c.fetchone()
        if not r or not r[0]:
            log.warning(f"[index-bars] {table}/{tk}: empty table — this is the "
                        f"appender, not the backfill; skipping.")
            continue
        window = append_window(r[0], now_et)
        if window is None:
            continue
        start, to = window
        if (to - r[0]).days > MAX_CATCHUP_DAYS:
            log.warning(f"[index-bars] {table}/{tk}: gap exceeds {MAX_CATCHUP_DAYS}d — "
                        f"appending the recent window only; older gap stays a recorded hole.")
        try:
            # list_aggs paginates past Polygon's base-aggregate limit (the
            # 2026-08-23 lesson); 40 days of 1m bars is ~15.6k rows.
            aggs = list(client.list_aggs(tk, multiplier=minutes, timespan="minute",
                                         from_=start.isoformat(), to=to.isoformat(),
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
            log.info(f"[index-bars] {table}/{tk}: nothing to append ({start}..{to}).")
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
    now_et = dt.datetime.now(et)
    conn = _conn()
    total = 0
    try:
        for table, tickers, minutes, last_start in TARGETS:
            total += _append_table(conn, client, table, tickers, minutes, last_start, et, now_et)
        return total
    finally:
        conn.close()
