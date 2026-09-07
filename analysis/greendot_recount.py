"""
The green-dot RECOUNT (2026-09-07). Two questions, one pass:

  1. Eric ruled Compass = Market Cipher B on every bar he checked — the
     eye's waves are Cipher B's 9/12/3 (channel EMA / average EMA / wt2
     SMA) while the engine's are LazyBear's 10/21/4. Every dot in the
     record was graded on 10/21/4. Recompute the 16D dots under 9/12/3 on
     the SAME fixed-anchor blocks (blocks_16d, no repaint), through the
     SAME wavetrend function (analysis.oscillator.wavetrend — one
     definition, two parameter sets), and tag Cipher's own two dots:
     any cross-up (green dot) and the BOTTOM BUY (cross-up with the
     light wave, wt1, below -60), beside our below-zero (wt2 <= 0) leg.
  2. The Beat-SPY afternoon found ticker-reuse splices (AI) and
     multi-year holes (TFIN) in daily_prices; the deep-dot census: 232
     of 1,538 tickers carry holes, 101 carry splices. Stamp
     beat_spy.series_defects on BOTH records — a defect inside the prior
     two years (the drawdown window) or the forward year (the outcome
     window) — so the deep cohort can be re-graded clean.

Pre-registered pass/fail (frozen here before any number): the deep
cohort (dd >= 50%, cross <= -30, below-zero) under 9/12/3 with defects
excluded must land in the same neighborhood as the 10/21/4 read on
median 6-month forward and %-positive, era-split; if it does, the
engine adopts 9/12/3 (a definition change, so it waits on this). The
bottom-buy flag grades as its own cohort. Dots are matched across
dialects within +/- one block.

Writes only greendot_dots_cb / greendot_recount_progress and the four
recount columns on greendot_dots. Chunked with resume; done when the
fleet is exhausted.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.greendot_recount")

MIN_DAILY_ROWS = 700
BOTTOM_BUY_LEVEL = -60.0      # Cipher B's bottom-buy dot: light wave below -60 at the cross
MATCH_BLOCKS = 1              # a CB dot within +/- one 16D block of an LB dot is the same event


def find_crosses(wt1, wt2):
    """Pure. Every wavetrend cross-up (wt1 crosses above wt2), each with
    Cipher's flags: below_zero = wt2 at the cross <= 0 (our leg);
    bottom_buy = wt1 at the cross < -60 (Cipher's). A hole in either
    wave at either bar is skipped, never treated as a value."""
    out = []
    for i in range(1, len(wt1)):
        a1, a2, b1, b2 = wt1[i - 1], wt2[i - 1], wt1[i], wt2[i]
        if None in (a1, a2, b1, b2):
            continue
        if a1 <= a2 and b1 > b2:
            out.append((i, b2 <= 0.0, b1 < BOTTOM_BUY_LEVEL))
    return out


def defect_flags(dot_date, defects):
    """Pure. (prior, fwd): a series defect inside the two years before
    the dot (the drawdown window) / inside the year after (the outcome
    window). defects: [(date, kind)]."""
    prior = any(dot_date - dt.timedelta(days=730) < d <= dot_date for d, _ in defects)
    fwd = any(dot_date < d <= dot_date + dt.timedelta(days=365) for d, _ in defects)
    return prior, fwd


def match_dots(a_idx, b_idx, tol=MATCH_BLOCKS):
    """Pure. Block indices of dots under dialect A and B. Returns
    (matched, only_a, only_b) counts; each B dot matches at most one A."""
    used, matched = set(), 0
    for i in a_idx:
        for j in b_idx:
            if j not in used and abs(i - j) <= tol:
                used.add(j)
                matched += 1
                break
    return matched, len(a_idx) - matched, len(b_idx) - matched


def _bars_16d(rows, cal_index):
    from analysis.greendot_study import blocks_16d
    dates = [r[0] for r in rows]
    blk = blocks_16d(dates, cal_index)
    bars, cur, cur_id = [], None, None
    for i, r in enumerate(rows):
        if blk[i] != cur_id:
            if cur is not None:
                bars.append(cur)
            cur_id = blk[i]
            cur = dict(end=dates[i], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                       v=float(r[5]), di=i, blk=blk[i])
        else:
            cur["h"] = max(cur["h"], float(r[2]))
            cur["l"] = min(cur["l"], float(r[3]))
            cur["c"] = float(r[4])
            cur["v"] += float(r[5])
            cur["end"] = dates[i]
            cur["di"] = i
    return bars                     # the trailing partial block is dropped — repaint guard


def _process_ticker(conn, ticker, cal_index):
    import pandas as pd
    from analysis.beat_spy import series_defects
    from analysis.greendot_study import bucket
    from analysis.oscillator import WT_CIPHER_B, WT_LAZYBEAR, wavetrend
    with conn.cursor() as c:
        c.execute("""SELECT trade_date, COALESCE(open, close), COALESCE(high, close), COALESCE(low, close),
                            close, COALESCE(volume, 0)
                     FROM daily_prices WHERE ticker=%s AND close IS NOT NULL ORDER BY trade_date""", (ticker,))
        rows = c.fetchall()
    if len(rows) < MIN_DAILY_ROWS:
        return 0, 0, 0
    dates = [r[0] for r in rows]
    closes = [float(r[4]) for r in rows]
    defects = series_defects(dates, closes)
    bars = _bars_16d(rows, cal_index)
    if len(bars) < 40:
        return 0, 0, len(defects)
    df = pd.DataFrame({"open": [b["o"] for b in bars], "high": [b["h"] for b in bars],
                       "low": [b["l"] for b in bars], "close": [b["c"] for b in bars],
                       "volume": [b["v"] for b in bars]},
                      index=pd.DatetimeIndex([pd.Timestamp(b["end"]) for b in bars]))

    def waves(params):
        w1, w2 = wavetrend(df, *params)
        return ([None if pd.isna(v) else float(v) for v in w1],
                [None if pd.isna(v) else float(v) for v in w2])

    lb1, lb2 = waves(WT_LAZYBEAR)
    cb1, cb2 = waves(WT_CIPHER_B)

    def outcomes(bi):
        di = bars[bi]["di"]
        d_date, px = dates[di], closes[di]
        hi2y = max(closes[max(0, di - 504):di + 1])
        dd = (hi2y - px) / hi2y if hi2y > 0 else 0.0
        f = lambda n: round((closes[di + n] / px - 1) * 100, 2) if di + n < len(closes) else None
        low6 = min(closes[di + 1: di + 127]) if di + 1 < len(closes) else None
        dist = round((low6 / px - 1) * 100, 2) if low6 is not None else None
        prior, fwd = defect_flags(d_date, defects)
        return d_date, px, dd, f(63), f(126), f(252), dist, prior, fwd

    n_cb = 0
    with conn.cursor() as c:
        for bi, below, bottom in find_crosses(cb1, cb2):
            d_date, px, dd, f63, f126, f252, dist, prior, fwd = outcomes(bi)
            c.execute("""INSERT INTO greendot_dots_cb
                         (ticker, dot_date, wt1_at_cross, wt2_at_cross, below_zero, bottom_buy, cross_depth,
                          drawdown_pct, dd_bucket, px_at_dot, dist_to_low_pct, fwd_63d_pct, fwd_126d_pct,
                          fwd_252d_pct, defect_prior, defect_fwd, era)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ticker, dot_date) DO NOTHING""",
                      (ticker, d_date, round(cb1[bi], 2), round(cb2[bi], 2), below, bottom, round(cb2[bi], 2),
                       round(dd * 100, 1), bucket(dd), px, dist, f63, f126, f252, prior, fwd,
                       "pre2016" if d_date < dt.date(2016, 1, 1) else "post2016"))
            n_cb += 1
        # the existing 10/21/4 record: stamp defects and the light wave / bottom-buy flag on its rows
        n_lb = 0
        for bi, below, bottom in find_crosses(lb1, lb2):
            if not below:
                continue                                   # greendot_dots holds below-zero dots only
            d_date = dates[bars[bi]["di"]]
            prior, fwd = defect_flags(d_date, defects)
            c.execute("""UPDATE greendot_dots SET defect_prior=%s, defect_fwd=%s, wt1_at_cross=%s, bottom_buy=%s
                         WHERE ticker=%s AND dot_date=%s""",
                      (prior, fwd, round(lb1[bi], 2), bottom, ticker, d_date))
            n_lb += c.rowcount
    conn.commit()
    return n_cb, n_lb, len(defects)


def run(batch: int = 400) -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT trade_date FROM daily_prices WHERE ticker='SPY' ORDER BY trade_date")
            cal_index = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT t.ticker FROM tickers t
                         WHERE COALESCE(t.delisted, false) = false
                           AND NOT EXISTS (SELECT 1 FROM greendot_recount_progress p WHERE p.ticker = t.ticker)
                         ORDER BY t.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            log.info("[greendot_recount] fleet complete.")
            readout(conn)
            return True
        for tk in todo:
            try:
                n_cb, n_lb, n_def = _process_ticker(conn, tk, cal_index)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot_recount] %s failed: %s", tk, str(e)[:300])
                n_cb, n_lb, n_def = 0, 0, 0
            with conn.cursor() as c:
                c.execute("""INSERT INTO greendot_recount_progress (ticker, n_dots_cb, n_dots_lb, n_defects)
                             VALUES (%s,%s,%s,%s) ON CONFLICT (ticker) DO NOTHING""", (tk, n_cb, n_lb, n_def))
            conn.commit()
        log.info("[greendot_recount] processed %d ticker(s) this pass.", len(todo))
        return False
    finally:
        conn.close()


READOUT_SQL = """
WITH lb AS (
  SELECT era, fwd_126d_pct, dist_to_low_pct, defect_prior, defect_fwd FROM greendot_dots
  WHERE drawdown_pct >= 50 AND cross_depth <= -30),
cb AS (
  SELECT era, fwd_126d_pct, dist_to_low_pct, defect_prior, defect_fwd, bottom_buy FROM greendot_dots_cb
  WHERE below_zero AND drawdown_pct >= 50 AND cross_depth <= -30),
bb AS (
  SELECT era, fwd_126d_pct, dist_to_low_pct, defect_prior, defect_fwd FROM greendot_dots_cb
  WHERE bottom_buy AND drawdown_pct >= 50)
SELECT 'LB 10/21/4 deep, all' AS cohort, era, count(*) FILTER (WHERE fwd_126d_pct IS NOT NULL) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY fwd_126d_pct)::numeric, 1) AS med_126,
       round(100.0 * avg((fwd_126d_pct > 0)::int) FILTER (WHERE fwd_126d_pct IS NOT NULL), 1) AS pct_pos,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dist_to_low_pct)::numeric, 1) AS med_dist_low
FROM lb GROUP BY era
UNION ALL
SELECT 'LB 10/21/4 deep, clean', era, count(*) FILTER (WHERE fwd_126d_pct IS NOT NULL),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY fwd_126d_pct)::numeric, 1),
       round(100.0 * avg((fwd_126d_pct > 0)::int) FILTER (WHERE fwd_126d_pct IS NOT NULL), 1),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dist_to_low_pct)::numeric, 1)
FROM lb WHERE NOT COALESCE(defect_prior, false) AND NOT COALESCE(defect_fwd, false) GROUP BY era
UNION ALL
SELECT 'CB 9/12/3 deep, clean', era, count(*) FILTER (WHERE fwd_126d_pct IS NOT NULL),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY fwd_126d_pct)::numeric, 1),
       round(100.0 * avg((fwd_126d_pct > 0)::int) FILTER (WHERE fwd_126d_pct IS NOT NULL), 1),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dist_to_low_pct)::numeric, 1)
FROM cb WHERE NOT defect_prior AND NOT defect_fwd GROUP BY era
UNION ALL
SELECT 'CB bottom-buy (wt1<-60), dd>=50, clean', era, count(*) FILTER (WHERE fwd_126d_pct IS NOT NULL),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY fwd_126d_pct)::numeric, 1),
       round(100.0 * avg((fwd_126d_pct > 0)::int) FILTER (WHERE fwd_126d_pct IS NOT NULL), 1),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dist_to_low_pct)::numeric, 1)
FROM bb WHERE NOT defect_prior AND NOT defect_fwd GROUP BY era
ORDER BY 1, 2
"""


def readout(conn):
    """Log the pre-registered comparison once the fleet is complete."""
    with conn.cursor() as c:
        c.execute(READOUT_SQL)
        for cohort, era, n, med, pos, dist in c.fetchall():
            log.info("[greendot_recount] %-42s %-8s n=%-6s med126=%-6s pos=%-5s%% med_dist_low=%s",
                     cohort, era, n, med, pos, dist)
