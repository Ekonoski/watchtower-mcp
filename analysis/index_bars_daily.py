"""
Daily appender for index_intraday_bars (2026-08-31, the frozen-table
find): the SPY/QQQ/IWM 15m record was only ever written by the
one-shot day-bias backfill, so it froze at 2026-08-21 the day that
backfill completed — cutting the flip-proximity study off exactly
before the chop days it was built to grade, and stranding the
RS-leader study's QQQ reference. A research table nobody appends to
is a `_social_block` with a date on it: every study reading it slowly
becomes a study of the past.

This module owns the table's freshness: resume from max(trade_date)
per ticker, fetch RTH 15m aggs, append. Runs 16:20 ET weekdays plus a
boot catch-up pass (cheap no-op when current). IWM's vendor history
is holey (recorded 2026-08-23) — appended anyway; holes stay visible.
Live books are unaffected either way: day_bias decides on
paper_spec_bars, never on this table.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.index_bars")

TICKERS = ("SPY", "QQQ", "IWM")
ET = "America/New_York"
MAX_CATCHUP_DAYS = 40      # windows stay far under the ~5k response cap


def run() -> int:
    """Append missing days per ticker; returns rows written."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    from zoneinfo import ZoneInfo

    client = get_client()
    if client is None:
        log.warning("[index-bars] no Polygon client — skipped.")
        return 0
    et = ZoneInfo(ET)
    today = dt.datetime.now(et).date()
    conn = _conn()
    total = 0
    try:
        for tk in TICKERS:
            with conn.cursor() as c:
                c.execute("SELECT max(trade_date) FROM index_intraday_bars "
                          "WHERE ticker=%s", (tk,))
                r = c.fetchone()
            if not r or not r[0]:
                log.warning(f"[index-bars] {tk}: empty table — this is the "
                            f"appender, not the backfill; skipping.")
                continue
            start = r[0] + dt.timedelta(days=1)
            if start > today:
                continue
            if (today - start).days > MAX_CATCHUP_DAYS:
                start = today - dt.timedelta(days=MAX_CATCHUP_DAYS)
                log.warning(f"[index-bars] {tk}: gap exceeds "
                            f"{MAX_CATCHUP_DAYS}d — appending the recent "
                            f"window only; older gap stays a recorded hole.")
            try:
                aggs = list(client.get_aggs(
                    tk, multiplier=15, timespan="minute",
                    from_=start.isoformat(), to=today.isoformat(),
                    limit=50000))
            except Exception as e:
                log.warning(f"[index-bars] {tk} fetch failed: {e}")
                continue
            rows = []
            for a in aggs:
                t = dt.datetime.fromtimestamp(a.timestamp / 1000,
                                              dt.timezone.utc).astimezone(et)
                if dt.time(9, 30) <= t.time() <= dt.time(15, 45):
                    rows.append((tk, t, t.date(), float(a.open),
                                 float(a.high), float(a.low), float(a.close),
                                 float(a.volume) if a.volume is not None
                                 else None))
            if rows:
                with conn.cursor() as c:
                    c.executemany(
                        """INSERT INTO index_intraday_bars
                           (ticker, ts, trade_date, open, high, low, close,
                            volume)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""", rows)
                conn.commit()
                total += len(rows)
                log.info(f"[index-bars] {tk}: +{len(rows)} bars through "
                         f"{rows[-1][2]}.")
            else:
                log.info(f"[index-bars] {tk}: nothing to append "
                         f"({start}..{today}).")
        return total
    finally:
        conn.close()
