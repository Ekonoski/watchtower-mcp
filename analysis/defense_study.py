"""
The 15-minute defense study (2026-08-21): does Eric's defense signature
— red volume contracting into a retest, a green volume-uptick off it —
separate outcomes at HISTORICAL retest episodes?

pattern_backtest already records retest_bar per episode, so the sample
is ready-made: daily-timeframe bullish episodes whose breakout came
back to retest the trigger. For each sampled episode the study resolves
the retest calendar date from daily_prices, fetches that day's 15m bars
from Polygon (a RESEARCH backtest — the reconstruction-is-not-tape rule
governs live trade grading, not research), runs the SAME find_defense
detector the live shadow uses, and stores the signature verdict beside
the episode's recorded outcome. Analysis is then one GROUP BY:
defended vs knife vs missed, against win_1r / realized_r.

Runs like the cipher study: boot-time seeder outside market hours,
resumes by episode id across boots, completion marker in
scheduler_job_claims. Caveats stated where the numbers will surface:
episode outcomes were graded from breakout-close entries (the signature
is a CONDITIONING variable here, not an entry re-price), and 15m volume
on thin names years back is only as good as the tape was.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.defense_study")

COMPLETE_MARKER = "defense_study_v1"
SAMPLE = 1200            # episodes; random, seeded by id ordering
LOOKBACK_MONTHS = 24     # 15m history depth we trust
BUDGET_S = 55 * 60       # one run's fetch budget; resumes next boot
ET = "America/New_York"


def _sample_episodes(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ticker, pattern, breakout_date, trigger_price,
                   invalid_level, retest_bar, win_1r, realized_r, outcome
            FROM pattern_backtest
            WHERE timeframe = 'daily' AND direction = 'bullish'
              AND retest_bar IS NOT NULL AND trigger_price > 0
              AND breakout_date >= CURRENT_DATE - INTERVAL '%s months'
              AND NOT EXISTS (SELECT 1 FROM defense_study d
                              WHERE d.episode_id = pattern_backtest.id)
            ORDER BY md5(id::text)
            LIMIT %s
            """ % (LOOKBACK_MONTHS, SAMPLE),
        )
        return cur.fetchall()


def _retest_date(conn, ticker, breakout_date, retest_bar):
    """retest_bar trading days after the breakout, on this ticker's own
    recorded calendar."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT trade_date FROM daily_prices
            WHERE ticker = %s AND trade_date > %s
            ORDER BY trade_date LIMIT %s
            """,
            (ticker, breakout_date, int(retest_bar)),
        )
        rows = cur.fetchall()
    return rows[-1][0] if len(rows) == int(retest_bar) else None


def _fetch_15m(client, ticker, day):
    """RTH 15m bars with volume for one historical day, oldest first."""
    from zoneinfo import ZoneInfo
    try:
        aggs = list(client.get_aggs(ticker, multiplier=15,
                                    timespan="minute",
                                    from_=day.isoformat(),
                                    to=day.isoformat(), limit=200))
    except Exception as e:
        log.debug(f"[defense-study] {ticker} {day} fetch failed: {e}")
        return None
    out = []
    for a in aggs:
        t = dt.datetime.fromtimestamp(a.timestamp / 1000,
                                      dt.timezone.utc).astimezone(
                                          ZoneInfo(ET))
        if dt.time(9, 30) <= t.time() <= dt.time(15, 45):
            out.append({"ts": t, "open": float(a.open),
                        "high": float(a.high), "low": float(a.low),
                        "close": float(a.close),
                        "volume": float(a.volume)})
    return out


def run() -> bool:
    """One budgeted pass; True when the sample is exhausted (marker
    written). Resumes by episode across boots."""
    from analysis.defense_shadow import find_defense
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn

    client = get_client()
    if client is None:
        log.warning("[defense-study] no Polygon client — skipped.")
        return False
    conn = _conn()
    t0 = time.time()
    done = 0
    try:
        episodes = _sample_episodes(conn)
        if not episodes:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,),
                )
            conn.commit()
            log.info(f"[defense-study] complete — marker {COMPLETE_MARKER}.")
            return True
        for (eid, tk, pattern, bdate, trig, stop, rbar,
             win1r, rr, outcome) in episodes:
            if time.time() - t0 > BUDGET_S:
                break
            trig = float(trig)
            stop = float(stop) if stop is not None else trig * 0.97
            rdate = _retest_date(conn, tk, bdate, rbar)
            rows = []
            if rdate is None:
                rows = [(v, "no_bars", None, None) for v in ("v1", "v2")]
            else:
                bars = _fetch_15m(client, tk, rdate)
                if not bars:
                    rows = [(v, "no_bars", None, None) for v in ("v1", "v2")]
                else:
                    touch_idx = next((i for i, b in enumerate(bars)
                                      if b["low"] <= trig), None)
                    if touch_idx is None:
                        rows = [(v, "no_touch", None, None)
                                for v in ("v1", "v2")]
                    else:
                        res = find_defense(bars, trig, stop, touch_idx)
                        rows = [(v,
                                 "knife" if r["status"] == "knife_skipped"
                                 else r["status"],
                                 r.get("px"), r.get("premium_pct"))
                                for v, r in res.items()]
            with conn.cursor() as cur:
                for variant, status, px, prem in rows:
                    cur.execute(
                        """
                        INSERT INTO defense_study
                            (episode_id, variant, ticker, retest_date,
                             status, defense_px, premium_pct, win_1r,
                             realized_r, outcome, pattern)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (episode_id, variant) DO NOTHING
                        """,
                        (eid, variant, tk, rdate, status, px, prem,
                         win1r, rr, outcome, pattern),
                    )
            conn.commit()
            done += 1
        log.info(f"[defense-study] pass: {done} episodes "
                 f"in {time.time()-t0:.0f}s.")
        return False
    finally:
        conn.close()
