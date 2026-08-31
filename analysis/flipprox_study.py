"""
The flip-proximity study (2026-09-01 — Eric, after the QQQ chop
beating: "how do I know it's hugging the flip and not going to fade it
or move through it? there is no way to know ahead of time"). The claim
that IS knowable gets graded: does the open's distance to the morning
gamma flip predict the day's CHARACTER (chop vs travel)?

Per SPY/QQQ day with both a recorded morning board (earliest
gex_levels row that calendar day) and stored 15m bars:

  dist_pct      |9:30 open - flip| / open, in percent
  flip_crosses  how many 15m CLOSES crossed the flip that day
  pdh_pdl_touch how many of the two prior-day extremes were touched
  range_travel  day range / total |15m close-to-close| travel —
                LOW = whipsaw (lots of movement, no ground covered)
  close_ret_bps open-to-close, for the record

Readout buckets dist_pct (<0.15 / 0.15-0.3 / 0.3-0.6 / >0.6) against
the character metrics. Small n stated ALWAYS (boards recorded since
mid-July); the table accumulates every session from here. Measurement
only — no gate until it earns one. Writes ONLY flipprox_days.
"""
import logging
import time

log = logging.getLogger("watchtower.flipprox")

COMPLETE_MARKER = "flipprox_study_v1"
TICKERS = ("SPY", "QQQ")
BUDGET_S = 10 * 60


def day_metrics(bars, flip):
    """Pure: bars = [(o, h, l, c), ...] 15m for one day. Returns
    (flip_crosses, range_travel, close_ret_bps). flip may be None ->
    crosses is None (a hole, not zero)."""
    o0 = bars[0][0]
    cN = bars[-1][3]
    hi = max(b[1] for b in bars)
    lo = min(b[2] for b in bars)
    travel = sum(abs(bars[i][3] - bars[i - 1][3]) for i in range(1, len(bars)))
    rng_travel = round((hi - lo) / travel, 3) if travel > 0 else None
    crosses = None
    if flip is not None:
        crosses = 0
        for i in range(1, len(bars)):
            if (bars[i][3] > flip) != (bars[i - 1][3] > flip):
                crosses += 1
    ret = round((cN / o0 - 1) * 1e4, 1)
    return crosses, rng_travel, ret


def run() -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            done_before = c.fetchone() is not None
        n = 0
        for tk in TICKERS:
            with conn.cursor() as c:
                c.execute("""SELECT DISTINCT b.trade_date
                             FROM index_intraday_bars b
                             WHERE b.ticker=%s
                               AND b.trade_date >= '2026-07-01'
                               AND NOT EXISTS (SELECT 1 FROM flipprox_days f
                                               WHERE f.ticker=%s
                                                 AND f.trade_date=b.trade_date)
                             ORDER BY b.trade_date""", (tk, tk))
                days = [r[0] for r in c.fetchall()]
            for d in days:
                if time.time() - t0 > BUDGET_S:
                    return False
                with conn.cursor() as c:
                    c.execute("""SELECT open, high, low, close
                                 FROM index_intraday_bars
                                 WHERE ticker=%s AND trade_date=%s
                                 ORDER BY ts""", (tk, d))
                    bars = [(float(a), float(b_), float(l), float(cl))
                            for a, b_, l, cl in c.fetchall()]
                    if len(bars) < 20:
                        continue
                    c.execute("""SELECT gamma_flip FROM gex_levels
                                 WHERE ticker=%s AND computed_at::date=%s
                                 ORDER BY computed_at LIMIT 1""", (tk, d))
                    r = c.fetchone()
                    flip = float(r[0]) if r and r[0] is not None else None
                    c.execute("""SELECT max(high), min(low)
                                 FROM index_intraday_bars
                                 WHERE ticker=%s AND trade_date =
                                   (SELECT max(trade_date)
                                    FROM index_intraday_bars
                                    WHERE ticker=%s AND trade_date < %s)""",
                              (tk, tk, d))
                    pr = c.fetchone()
                open_px = bars[0][0]
                crosses, rng_travel, ret = day_metrics(bars, flip)
                dist = (round(abs(open_px - flip) / open_px * 100, 3)
                        if flip is not None else None)
                touch = None
                if pr and pr[0] is not None:
                    pdh, pdl = float(pr[0]), float(pr[1])
                    hi = max(b[1] for b in bars)
                    lo = min(b[2] for b in bars)
                    touch = int(hi >= pdh) + int(lo <= pdl)
                with conn.cursor() as c:
                    c.execute("""INSERT INTO flipprox_days
                        (ticker, trade_date, open_px, flip_px, dist_pct,
                         flip_crosses, pdh_pdl_touch, range_travel,
                         close_ret_bps)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING""",
                        (tk, d, round(open_px, 4), flip, dist, crosses,
                         touch, rng_travel, ret))
                conn.commit()
                n += 1
        if not done_before:
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,))
            conn.commit()
        log.info("[flipprox] graded %d ticker-day(s).", n)
        return True
    finally:
        conn.close()
