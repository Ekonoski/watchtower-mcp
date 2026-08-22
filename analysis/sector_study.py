"""
The sector-rotation study (2026-08-22): does the sector's money flow at
breakout time condition our graded pattern outcomes? Eric's thesis —
"if money rotation is flowing out of that sector, it doesn't really
matter what pattern shows up" — measured before it gates anything,
same as the cipher (rides as a tag) and the defense signature (shadow).

Entirely from recorded data: daily_prices + tickers.sector build the
sector_rs_daily cache (analysis/sector_rs.py — the same definition the
spec writer's tag reads), then every graded daily bullish episode joins
to its sector's read on its own breakout date. Episodes whose ticker
has no sector mapping, or whose date the cache can't cover, are kept
as hole rows (NULL rs) — recorded, never dropped. Analysis is one
GROUP BY over rank/rs buckets vs win_1r / realized_r (cap outliers at
10R when averaging — the BW-3D lesson).

Caveats stated where the numbers will surface: outcomes were graded on
breakout-close entries (conditioning read, not an entry re-price), and
sector medians before ~2024 thin out with daily_prices coverage.
"""
import logging
import time

log = logging.getLogger("watchtower.sector_study")

COMPLETE_MARKER = "sector_study_v1"
LOOKBACK_MONTHS = 24     # match the episode grades the desk trusts
BUDGET_S = 50 * 60


def _month_starts(conn):
    """Months in the study window missing from the cache (the current
    month always rebuilds — DO NOTHING makes that cheap)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH months AS (
                SELECT generate_series(
                    date_trunc('month', CURRENT_DATE - INTERVAL '%s months'),
                    date_trunc('month', CURRENT_DATE),
                    INTERVAL '1 month')::date AS m
            )
            SELECT m FROM months
            WHERE m = date_trunc('month', CURRENT_DATE)::date
               OR NOT EXISTS (
                    SELECT 1 FROM sector_rs_daily r
                    WHERE r.trade_date >= m
                      AND r.trade_date < m + INTERVAL '1 month'
                    LIMIT 1)
            ORDER BY m
            """ % LOOKBACK_MONTHS
        )
        return [r[0] for r in cur.fetchall()]


def run() -> bool:
    """One budgeted pass; True when cache and study rows are complete
    (marker written). Resumes by month / by episode across boots."""
    import datetime as dt
    from analysis.sector_rs import build_range
    from screen.reversal_screen import _conn

    conn = _conn()
    t0 = time.time()
    try:
        for m in _month_starts(conn):
            if time.time() - t0 > BUDGET_S:
                log.info("[sector-study] budget hit mid-cache; resuming "
                         "next boot.")
                return False
            end = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1) \
                - dt.timedelta(days=1)
            n = build_range(conn, m, min(end, dt.date.today()))
            log.info(f"[sector-study] cache {m:%Y-%m}: +{n} rows")

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sector_study
                    (episode_id, ticker, sector, breakout_date, pattern,
                     rs_1m, rs_1w, rank_1m, win_1r, realized_r, outcome)
                SELECT pb.id, pb.ticker, t.sector, pb.breakout_date,
                       pb.pattern, r.rs_1m, r.rs_1w, r.rank_1m,
                       pb.win_1r, pb.realized_r, pb.outcome
                FROM pattern_backtest pb
                LEFT JOIN tickers t ON t.ticker = pb.ticker
                LEFT JOIN sector_rs_daily r
                       ON r.sector = t.sector
                      AND r.trade_date = pb.breakout_date
                WHERE pb.timeframe = 'daily' AND pb.direction = 'bullish'
                  AND pb.outcome IS NOT NULL
                  AND pb.breakout_date >=
                      CURRENT_DATE - INTERVAL '%s months'
                  AND NOT EXISTS (SELECT 1 FROM sector_study s
                                  WHERE s.episode_id = pb.id)
                """ % LOOKBACK_MONTHS
            )
            added = cur.rowcount
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scheduler_job_claims (job_name, run_date) "
                "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                (COMPLETE_MARKER,),
            )
        conn.commit()
        log.info(f"[sector-study] complete — +{added} episode rows, "
                 f"marker {COMPLETE_MARKER}.")
        return True
    finally:
        conn.close()
