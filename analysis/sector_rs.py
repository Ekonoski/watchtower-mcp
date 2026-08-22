"""
Sector relative strength, breadth-style (2026-08-22 — Eric: "you can
pick a great stock but be in the wrong sector while money rotation is
flowing out").

One definition, three readers (the find_defense precedent): the daily
cache feeds the historical study, the spec writer's measurement tag,
and any future gate a graded result earns. MEDIAN-stock based, like the
sector heatmap — a sector's read is its typical stock vs the market's
typical stock, deliberately immune to one mega-cap dragging the group.

  rs_1m / rs_1w: sector median trailing 21d/5d return MINUS the
  all-stock median over the same window (relative, so a tape-wide rout
  doesn't paint every sector as an outflow).
  rank_1m: 1 = strongest sector that day.

The cache is computed from recorded daily_prices only. A date missing
from the cache is a hole and tags render it as one — never a zero.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.sector_rs")

LOOKBACK_1M = 21      # trading days — the rotation month
LOOKBACK_1W = 5       # trading days — the rotation week
STALE_DAYS = 7        # tag older than this carries stale=True

# lag() needs LOOKBACK_1M prior trading rows per ticker; 60 calendar
# days of head-room covers ~41 trading days.
_BUILD_SQL = """
WITH px AS (
    SELECT ticker, trade_date, close,
           lag(close, %(l1m)s) OVER (PARTITION BY ticker ORDER BY trade_date) AS c1m,
           lag(close, %(l1w)s) OVER (PARTITION BY ticker ORDER BY trade_date) AS c1w
    FROM daily_prices
    WHERE trade_date BETWEEN %(start)s::date - INTERVAL '60 days' AND %(end)s
      AND close IS NOT NULL AND close > 0
),
rets AS (
    SELECT p.trade_date, t.sector,
           p.close / NULLIF(p.c1m, 0) - 1 AS r1m,
           p.close / NULLIF(p.c1w, 0) - 1 AS r1w
    FROM px p JOIN tickers t ON t.ticker = p.ticker
    WHERE p.trade_date BETWEEN %(start)s AND %(end)s
      AND t.sector IS NOT NULL AND t.sector <> ''
      AND p.c1m IS NOT NULL AND p.c1w IS NOT NULL
),
mkt AS (
    SELECT trade_date,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY r1m) AS m1m,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY r1w) AS m1w
    FROM rets GROUP BY trade_date
),
sec AS (
    SELECT trade_date, sector,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY r1m) AS s1m,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY r1w) AS s1w,
           count(*) AS n
    FROM rets GROUP BY trade_date, sector
)
INSERT INTO sector_rs_daily (trade_date, sector, rs_1m, rs_1w, rank_1m, n_tickers)
SELECT s.trade_date, s.sector, s.s1m - m.m1m, s.s1w - m.m1w,
       rank() OVER (PARTITION BY s.trade_date ORDER BY s.s1m - m.m1m DESC),
       s.n
FROM sec s JOIN mkt m USING (trade_date)
ON CONFLICT (trade_date, sector) DO NOTHING
"""


def build_range(conn, start: dt.date, end: dt.date) -> int:
    """Fill sector_rs_daily for [start, end] from recorded daily_prices.
    Idempotent (DO NOTHING); returns rows inserted."""
    with conn.cursor() as cur:
        cur.execute(_BUILD_SQL, {"l1m": LOOKBACK_1M, "l1w": LOOKBACK_1W,
                                 "start": start, "end": end})
        n = cur.rowcount
    conn.commit()
    return n


def ensure_recent(conn, days_back: int = 10) -> int:
    """Self-healing daily upkeep for the tag: fill any cache gap in the
    trailing window. Cheap (a few trading days), no dedicated cron."""
    today = dt.date.today()
    return build_range(conn, today - dt.timedelta(days=days_back), today)


def sector_tag(sector, row, today=None) -> dict:
    """Pure tag builder. row: (trade_date, rs_1m, rs_1w, rank_1m,
    n_sectors) or None. Holes carry a reason, never fabricated zeros;
    the tag stamps its own as-of date — freshness per row."""
    if not sector:
        return {"sector": None, "reason": "no_sector_mapping"}
    if row is None:
        return {"sector": sector, "reason": "rs_unavailable"}
    asof, rs_1m, rs_1w, rank_1m, of = row
    tag = {"sector": sector, "asof": asof.isoformat(),
           "rs_1m": round(float(rs_1m), 5), "rs_1w": round(float(rs_1w), 5),
           "rank_1m": int(rank_1m), "of": int(of)}
    today = today or dt.date.today()
    if (today - asof).days > STALE_DAYS:
        tag["stale"] = True
    return tag


def sector_state_for(conn, ticker: str) -> dict:
    """The measurement tag for one spec: the ticker's sector's freshest
    cached read. Read-only against the cache — never computes inline,
    so a cache gap surfaces as a visible hole instead of a silent
    per-spec recompute."""
    with conn.cursor() as cur:
        cur.execute("SELECT sector FROM tickers WHERE ticker=%s", (ticker,))
        r = cur.fetchone()
        sector = r[0] if r and r[0] else None
        if not sector:
            return sector_tag(None, None)
        cur.execute(
            """
            SELECT r.trade_date, r.rs_1m, r.rs_1w, r.rank_1m,
                   (SELECT count(*) FROM sector_rs_daily x
                    WHERE x.trade_date = r.trade_date) AS of
            FROM sector_rs_daily r
            WHERE r.sector = %s ORDER BY r.trade_date DESC LIMIT 1
            """,
            (sector,),
        )
        row = cur.fetchone()
    return sector_tag(sector, row)
