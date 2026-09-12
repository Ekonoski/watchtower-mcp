"""
The sector-leader study (2026-09-12 — the X post Eric attached to the
two-test workbook, pasted after x.com proved egress-blocked: "Figure
out where money is moving first … What is outperforming SPY and QQQ?
What is holding the 8/21? What is breaking out of a base? Where is
volume expanding? What is green while the market is red? What is
making new highs? Once I find the strongest sector, I go inside it and
find the leaders. Then I wait for my setup. Find the sector. Find the
leader. Wait for the break and retest.").

Pre-registered before any number. Every daily bullish episode in
pattern_backtest (2005→, the break-and-retest population the desk
grades everything on) is stamped with the post's legs read at the
PRIOR close — the last stored bar strictly before the breakout date —
so nothing on the breakout bar leaks in:

  SECTOR (the post's own definition: a cap-weighted sector ETF, not the
  desk's breadth RS — a DIFFERENT definition, stated):
    etf          the ticker's sector mapped to its SPDR (ETF_BY_SECTOR).
                 Funds are not stocks in a sector: a ticker whose
                 industry is Asset Management / Shell Companies is a
                 FUND row (fund=true, etf NULL) — 39,902 of 166,555
                 episodes ride on ETFs the scanner also patterns, and
                 they are holes here, never "Financial Services".
    etf_rs21/63  ETF 21/63-bar return minus SPY's, same date.
    etf_rank21   the ETF's rank by rs21 among the SPDRs with data that
                 day (1 = strongest; n_etfs beside it — XLRE exists
                 from 2015-10, XLC from 2018-06, so early rows rank
                 among 9 or 10).
    etf_above    ETF close above BOTH its 8 and 21 EMA ("holding the
                 8/21").
    etf_near_hi  ETF close within 3% of its 252-bar high ("new highs").
    etf_beats_qqq  ETF 21-bar return above QQQ's (post: "outperforming
                 SPY and QQQ"; QQQ's record starts 2011-03, NULL before).
  LEADER (inside the sector):
    pct21/pct63  the stock's 21/63-bar return percentile among ALL
                 non-fund tickers of its sector with bars that day
                 (stage 2, one SQL cross-section per sector; n_peers
                 beside it). ≥ 0.8 = leader.
    stk_rs_etf21 stock 21-bar return minus its ETF's (leading its
                 sector); stk_rs_spy21 minus SPY's.
    stk_above    stock close above both its 8 and 21 EMA.
    stk_near_hi  within 3% of its 252-bar high (NULL under 126 bars).
    vol_exp      avg volume last 20 bars / last 60 ("volume expanding").
    green_red10  bars in the last 10 where the stock closed up and SPY
                 closed down ("green while the market is red").
    defect       a series splice/gap inside the prior 63 bars
                 (beat_spy.series_defects, imported) — the row is a hole
                 for the RS legs.

Outcomes are the episode's own: win_1r, realized_r (cap ±10 and guard
NULL at readout — the LEAST/GREATEST trap). Era = pre/post 2016. BAR,
frozen: the post's stacked rule (ETF rank ≤ 3 AND etf_above AND stock
pct21 ≥ 0.8 AND stk_above) must beat the pool on realized_r in BOTH
eras with n ≥ 200 per era, and each leg alone must show a monotone
gradient in the same direction in both eras, before any of it becomes
a tag. Caveats where the numbers surface: breakout-close entries (the
desk buys retests — a conditioning read), survivors-plus-delisted-with-
bars universe, no costs, cap-weighted ETF vs breadth RS.

Writes ONLY sector_leader_events (stage 1 inserts, stage 2 UPDATEs the
percentile columns) and sector_leader_progress. Markers:
sector_leader_v1 (stage 1), sector_leader_xs:<sector> (per sector),
sector_leader_xs_v1 (complete → readout logged).
"""
import bisect
import datetime as dt
import logging
import time

from analysis.beat_spy import series_defects
from analysis.tapeentry_study import ema_series

log = logging.getLogger("watchtower.sector_leader")

STAGE1_MARKER = "sector_leader_v1"
XS_MARKER = "sector_leader_xs_v1"
BUDGET_S = 25 * 60
NEAR_HI_TOL = 0.03
HI_BARS = 252
HI_MIN_BARS = 126
DEFECT_LOOKBACK = 63

ETF_BY_SECTOR = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Basic Materials": "XLB",
    "Energy": "XLE",
    "Communication Services": "XLC",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Financial Services": "XLF",
}
ETFS = tuple(sorted(set(ETF_BY_SECTOR.values())))
FUND_INDUSTRY_PREFIXES = ("Asset Management", "Shell Companies")


# ── pure cores ───────────────────────────────────────────────────────

def is_fund(industry):
    """Pure. The tickers table files every ETF under Financial Services /
    Asset Management (a vendor default). A fund has no sector read."""
    return bool(industry) and any(industry.startswith(p) for p in FUND_INDUSTRY_PREFIXES)


def sector_etf(sector, industry):
    """Pure. The post's sector proxy for a stock; None for funds and
    unmapped sectors (a hole, never a default)."""
    if is_fund(industry):
        return None
    return ETF_BY_SECTOR.get(sector)


def prior_index(dates, breakout_date):
    """Pure. Index of the last bar STRICTLY before breakout_date — the
    prior close, the only bar the post's weekend scan could have read.
    None when no bar precedes it."""
    i = bisect.bisect_left(dates, breakout_date) - 1
    return i if i >= 0 else None


def ret(closes, i, n):
    """Pure. closes[i] / closes[i-n] - 1; None inside warmup or on a
    non-positive base."""
    if i is None or i - n < 0 or closes[i - n] <= 0:
        return None
    return closes[i] / closes[i - n] - 1.0


def above_8_21(closes, e8, e21, i):
    """Pure. Close above BOTH EMAs; None before the 21 EMA is seeded."""
    if i is None or i < 21 or e8[i] is None or e21[i] is None:
        return None
    return closes[i] > e8[i] and closes[i] > e21[i]


def near_high(closes, i, n=HI_BARS, min_bars=HI_MIN_BARS, tol=NEAR_HI_TOL):
    """Pure. Close within tol of the max close over the last n bars
    (fewer when the series is shorter, but at least min_bars — else None)."""
    if i is None or i + 1 < min_bars:
        return None
    lo = max(0, i - n + 1)
    hi = max(closes[lo:i + 1])
    return hi > 0 and closes[i] >= hi * (1.0 - tol)


def vol_expansion(vols, i, fast=20, slow=60):
    """Pure. avg(vols[i-19..i]) / avg(vols[i-59..i]); None inside warmup
    or when the slow average is zero (a dead tape is not expanding)."""
    if i is None or i + 1 < slow:
        return None
    s = sum(vols[i - slow + 1:i + 1]) / slow
    if s <= 0:
        return None
    return (sum(vols[i - fast + 1:i + 1]) / fast) / s


def green_red_days(dates, closes, spy_by_date, i, n=10):
    """Pure. Bars in the last n (ending at i) where the stock closed UP
    and SPY closed DOWN on the same date. A date SPY has no bar for
    (or whose prior stock bar has none) is skipped — it cannot count
    either way. None when fewer than n stock bars precede i."""
    if i is None or i < n:
        return None
    cnt = 0
    for k in range(i - n + 1, i + 1):
        s1, s0 = spy_by_date.get(dates[k]), spy_by_date.get(dates[k - 1])
        if s1 is None or s0 is None:
            continue
        if closes[k] > closes[k - 1] and s1 < s0:
            cnt += 1
    return cnt


def rank_among(values):
    """Pure. {key: rank} by value descending (1 = highest); keys with a
    None value are left out and do not count toward n."""
    known = [(v, k) for k, v in values.items() if v is not None]
    known.sort(key=lambda kv: -kv[0])
    return {k: r + 1 for r, (_, k) in enumerate(known)}


def has_defect(defect_dates, dates, i, lookback=DEFECT_LOOKBACK):
    """Pure. True when a stamped defect falls inside (dates[i-lookback], dates[i]]."""
    if i is None or not defect_dates:
        return False
    lo = dates[max(0, i - lookback)]
    return any(lo < d <= dates[i] for d in defect_dates)


def era(d):
    return "pre2016" if d < dt.date(2016, 1, 1) else "post2016"


# ── series helpers ───────────────────────────────────────────────────

class _Series:
    """A stored daily series with its EMAs, indexable by date."""

    def __init__(self, rows):
        self.dates = [r[0] for r in rows]
        self.closes = [float(r[1]) for r in rows]
        self.e8 = ema_series(self.closes, 8)
        self.e21 = ema_series(self.closes, 21)
        self.by_date = dict(zip(self.dates, self.closes))

    def at_or_before(self, d):
        i = bisect.bisect_right(self.dates, d) - 1
        return i if i >= 0 else None


def _load_series(conn, ticker):
    with conn.cursor() as c:
        c.execute("SELECT trade_date, close FROM daily_prices WHERE ticker=%s AND close IS NOT NULL "
                  "ORDER BY trade_date", (ticker,))
        rows = c.fetchall()
    return _Series(rows) if rows else None


def etf_legs(bench, d, cache):
    """ETF legs for every SPDR at date d (each ETF read at its own bar
    at-or-before d), cached per date: {etf: dict} plus the rank map."""
    if d in cache:
        return cache[d]
    spy, qqq = bench["SPY"], bench["QQQ"]
    si = spy.at_or_before(d)
    spy21, spy63 = ret(spy.closes, si, 21), ret(spy.closes, si, 63)
    qi = qqq.at_or_before(d) if qqq else None
    qqq21 = ret(qqq.closes, qi, 21) if qi is not None else None
    legs = {}
    for etf in ETFS:
        s = bench.get(etf)
        i = s.at_or_before(d) if s else None
        r21, r63 = (ret(s.closes, i, 21), ret(s.closes, i, 63)) if i is not None else (None, None)
        legs[etf] = {
            "r21": r21,
            "rs21": None if r21 is None or spy21 is None else r21 - spy21,
            "rs63": None if r63 is None or spy63 is None else r63 - spy63,
            "above": above_8_21(s.closes, s.e8, s.e21, i) if s else None,
            "near_hi": near_high(s.closes, i) if s else None,
            "beats_qqq": None if r21 is None or qqq21 is None else r21 > qqq21,
        }
    ranks = rank_among({e: v["rs21"] for e, v in legs.items()})
    out = {"legs": legs, "ranks": ranks, "n_etfs": len(ranks), "spy21": spy21}
    cache[d] = out
    return out


# ── stage 1: per-ticker stock + ETF legs ─────────────────────────────

def _process_ticker(conn, tk, sector, industry, bench, cache):
    with conn.cursor() as c:
        c.execute("""SELECT id, breakout_date, pattern, retest_bar, win_1r, realized_r
                     FROM pattern_backtest WHERE ticker=%s AND timeframe='daily' AND direction='bullish'
                       AND outcome IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM sector_leader_events e WHERE e.episode_id=pattern_backtest.id)
                     ORDER BY breakout_date""", (tk,))
        eps = c.fetchall()
    if not eps:
        return 0
    with conn.cursor() as c:
        c.execute("SELECT trade_date, close, COALESCE(volume, 0) FROM daily_prices WHERE ticker=%s "
                  "AND close IS NOT NULL ORDER BY trade_date", (tk,))
        rows = c.fetchall()
    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    vols = [float(r[2]) for r in rows]
    e8, e21 = ema_series(closes, 8), ema_series(closes, 21)
    defect_dates = [d for d, _ in series_defects(dates, closes)]
    fund = is_fund(industry)
    etf = sector_etf(sector, industry)
    spy = bench["SPY"]
    out = []
    for eid, bd, pattern, retest_bar, win_1r, rr in eps:
        i = prior_index(dates, bd)
        pd_ = dates[i] if i is not None else None
        r21, r63 = ret(closes, i, 21), ret(closes, i, 63)
        el = etf_legs(bench, pd_, cache) if pd_ is not None else None
        e = el["legs"].get(etf) if (el and etf) else None
        spy21 = el["spy21"] if el else None
        out.append((
            eid, tk, None if fund else sector, etf, fund, bd, pd_, era(bd), pattern, retest_bar, win_1r, rr,
            e["rs21"] if e else None, e["rs63"] if e else None,
            el["ranks"].get(etf) if (el and etf) else None, el["n_etfs"] if el else None,
            e["above"] if e else None, e["near_hi"] if e else None, e["beats_qqq"] if e else None,
            r21, r63,
            None if (r21 is None or not e or e["r21"] is None) else r21 - e["r21"],
            None if (r21 is None or spy21 is None) else r21 - spy21,
            above_8_21(closes, e8, e21, i), near_high(closes, i), vol_expansion(vols, i),
            green_red_days(dates, closes, spy.by_date, i), has_defect(defect_dates, dates, i),
        ))
    with conn.cursor() as c:
        c.executemany("""INSERT INTO sector_leader_events
            (episode_id, ticker, sector, etf, fund, breakout_date, prior_date, era, pattern, retest_bar,
             win_1r, realized_r, etf_rs21, etf_rs63, etf_rank21, n_etfs, etf_above, etf_near_hi,
             etf_beats_qqq, stk_ret21, stk_ret63, stk_rs_etf21, stk_rs_spy21, stk_above, stk_near_hi,
             vol_exp, green_red10, defect)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (episode_id) DO NOTHING""", out)
    return len(out)


def _stage1(conn, t0):
    bench = {}
    for tk in ("SPY", "QQQ") + ETFS:
        bench[tk] = _load_series(conn, tk)
    if bench["SPY"] is None:
        raise RuntimeError("SPY series missing — the benchmark is a hole, not a zero")
    cache = {}
    while True:
        if time.time() - t0 > BUDGET_S:
            log.info("[sector_leader] budget hit in stage 1; resuming next pass.")
            return False
        with conn.cursor() as c:
            c.execute("""SELECT DISTINCT pb.ticker, t.sector, t.industry
                         FROM pattern_backtest pb LEFT JOIN tickers t ON t.ticker=pb.ticker
                         WHERE pb.timeframe='daily' AND pb.direction='bullish' AND pb.outcome IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM sector_leader_progress p WHERE p.ticker=pb.ticker)
                         ORDER BY pb.ticker LIMIT 100""")
            todo = c.fetchall()
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                          "ON CONFLICT DO NOTHING", (STAGE1_MARKER,))
            conn.commit()
            log.info("[sector_leader] stage 1 complete — marker %s.", STAGE1_MARKER)
            return True
        for tk, sector, industry in todo:
            try:
                n = _process_ticker(conn, tk, sector, industry, bench, cache)
            except Exception as e:
                conn.rollback()
                log.warning("[sector_leader] %s failed: %s", tk, str(e)[:300])
                n = -1                                   # a failed ticker is recorded as one, not skipped as zero
            with conn.cursor() as c:
                c.execute("INSERT INTO sector_leader_progress (ticker, n_events) VALUES (%s,%s) "
                          "ON CONFLICT (ticker) DO NOTHING", (tk, n))
            conn.commit()
        log.info("[sector_leader] stage 1: %d ticker(s) this batch.", len(todo))


# ── stage 2: the sector cross-section (percentile among peers) ───────

XS_SQL = """
WITH u AS (SELECT ticker FROM tickers
           WHERE sector = %s
             AND NOT (COALESCE(industry, '') LIKE 'Asset Management%%' OR industry = 'Shell Companies')),
r AS (SELECT d.ticker, d.trade_date,
             d.close / NULLIF(LAG(d.close, 21) OVER w, 0) - 1 AS ret21,
             d.close / NULLIF(LAG(d.close, 63) OVER w, 0) - 1 AS ret63
      FROM daily_prices d JOIN u ON u.ticker = d.ticker
      WHERE d.close IS NOT NULL
      WINDOW w AS (PARTITION BY d.ticker ORDER BY d.trade_date)),
p AS (SELECT ticker, trade_date,
             percent_rank() OVER (PARTITION BY trade_date ORDER BY ret21) AS pr21,
             percent_rank() OVER (PARTITION BY trade_date ORDER BY ret63) AS pr63,
             count(*) OVER (PARTITION BY trade_date) AS n
      FROM r WHERE ret21 IS NOT NULL AND ret63 IS NOT NULL)
UPDATE sector_leader_events e
   SET pct21 = p.pr21, pct63 = p.pr63, n_peers = p.n
  FROM p
 WHERE e.sector = %s AND e.ticker = p.ticker AND e.prior_date = p.trade_date
"""


def _stage2(conn, t0):
    for sector in ETF_BY_SECTOR:
        marker = f"sector_leader_xs:{sector}"
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (marker,))
            if c.fetchone():
                continue
        if time.time() - t0 > BUDGET_S:
            log.info("[sector_leader] budget hit in stage 2; resuming next pass.")
            return False
        ts = time.time()
        with conn.cursor() as c:
            c.execute(XS_SQL, (sector, sector))
            n = c.rowcount
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                      "ON CONFLICT DO NOTHING", (marker,))
        conn.commit()
        log.info("[sector_leader] cross-section %s: %d rows stamped in %.0fs.", sector, n, time.time() - ts)
    with conn.cursor() as c:
        c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                  "ON CONFLICT DO NOTHING", (XS_MARKER,))
    conn.commit()
    log.info("[sector_leader] complete — marker %s.", XS_MARKER)
    readout(conn)
    return True


def run() -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (XS_MARKER,))
            if c.fetchone():
                return True
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (STAGE1_MARKER,))
            stage1_done = bool(c.fetchone())
        if not stage1_done and not _stage1(conn, t0):
            return False
        return _stage2(conn, t0)
    finally:
        conn.close()


READOUT_SQL = """
-- the post's stacked rule vs the pool, both eras, NULL-guarded, R capped ±10
WITH e AS (
  SELECT era, win_1r,
         LEAST(GREATEST(realized_r, -10), 10) AS r,
         (etf_rank21 <= 3 AND etf_above AND pct21 >= 0.8 AND stk_above) AS post_rule,
         etf_rank21, pct21, stk_above, etf_above, vol_exp, green_red10, stk_near_hi, etf_near_hi
  FROM sector_leader_events
  WHERE NOT fund AND NOT defect AND realized_r IS NOT NULL AND etf_rank21 IS NOT NULL AND pct21 IS NOT NULL)
SELECT era, 'pool' AS cell, count(*) n, round(100.0*avg(win_1r::int),1) win1r, round(avg(r),3) avg_r FROM e GROUP BY era
UNION ALL
SELECT era, 'post_rule', count(*), round(100.0*avg(win_1r::int),1), round(avg(r),3) FROM e WHERE post_rule GROUP BY era
UNION ALL
SELECT era, 'etf_top3', count(*), round(100.0*avg(win_1r::int),1), round(avg(r),3) FROM e WHERE etf_rank21 <= 3 GROUP BY era
UNION ALL
SELECT era, 'etf_bottom3', count(*), round(100.0*avg(win_1r::int),1), round(avg(r),3) FROM e WHERE etf_rank21 >= 9 GROUP BY era
UNION ALL
SELECT era, 'leader_q5', count(*), round(100.0*avg(win_1r::int),1), round(avg(r),3) FROM e WHERE pct21 >= 0.8 GROUP BY era
UNION ALL
SELECT era, 'laggard_q1', count(*), round(100.0*avg(win_1r::int),1), round(avg(r),3) FROM e WHERE pct21 < 0.2 GROUP BY era
ORDER BY cell, era;
"""


def readout(conn):
    try:
        with conn.cursor() as c:
            c.execute(READOUT_SQL)
            for row in c.fetchall():
                log.info("[sector_leader] readout %s", row)
    except Exception as e:
        conn.rollback()
        log.warning("[sector_leader] readout failed: %s", str(e)[:300])
