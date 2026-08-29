"""
The 16D RED-dot study — the green dot's mirror (2026-08-29, off the
NVDA bellwether read: three wicks at the walls but "nvda does not
have a red dot on the 16 day" — the missing graded object is the
bearish cross, and its use is the dot sleeve's EXIT rule plus
top-confirmation on holds. NOT a short signal: shorts are retired).

Pre-registered spec, frozen before any number:
  event      16D wavetrend cross-DOWN with the cross ABOVE zero
             (wt2 >= 0 at the cross bar); cross HEIGHT stored so the
             elevation throttle is a readout cut (>= +30 mirrors the
             green side's load-bearing depth leg), never a filter
             baked into the record.
  condition  run-up vs the trailing 2-year LOW at the dot, bucketed
             (<50 / 50-100 / 100-200 / 200%+) — the drawdown
             condition mirrored.
  outcomes   fwd 63/126/252-day returns from the dot's real close,
             plus distance to the FORWARD 6-month HIGH (what an exit
             at the dot gives up — the cost side the exit rule must
             answer for), era-split at readout, random-day baselines
             on the same cohort at readout.

Detection REUSES the graded detector by negation: a cross-down of
(wt1, wt2) above zero IS find_dots on the negated series — pinned by
test, so the mirror can never drift from the original. Fixed-anchor
16D bars via blocks_16d (no repaint). Writes only reddot_dots /
reddot_progress.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.reddot")

MIN_DAILY_ROWS = 700
RU_BUCKETS = ((2.00, "gte200"), (1.00, "b100_200"), (0.50, "b50_100"),
              (-1.0, "lt50"))


def ru_bucket(ru):
    for thresh, name in RU_BUCKETS:
        if ru >= thresh:
            return name
    return "lt50"


def find_red_dots(wt1, wt2):
    """Pure. Indices of wavetrend cross-DOWNS where the cross is above
    zero — find_dots on the negated series, so the graded detector is
    reused, never reimplemented."""
    from analysis.greendot_study import find_dots
    n1 = [None if v is None else -v for v in wt1]
    n2 = [None if v is None else -v for v in wt2]
    return find_dots(n1, n2)


def _process_ticker(conn, ticker, cal_index):
    import pandas as pd
    from analysis.greendot_study import blocks_16d
    from analysis.oscillator import compute_oscillator
    with conn.cursor() as c:
        c.execute("""SELECT trade_date, COALESCE(open, close),
                            COALESCE(high, close), COALESCE(low, close),
                            close, COALESCE(volume, 0)
                     FROM daily_prices
                     WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (ticker,))
        rows = c.fetchall()
    if len(rows) < MIN_DAILY_ROWS:
        return 0
    dates = [r[0] for r in rows]
    closes = [float(r[4]) for r in rows]
    blk = blocks_16d(dates, cal_index)
    bars, cur, cur_id = [], None, None
    for i, r in enumerate(rows):
        if blk[i] != cur_id:
            if cur is not None:
                bars.append(cur)
            cur_id = blk[i]
            cur = dict(end=dates[i], o=float(r[1]), h=float(r[2]),
                       l=float(r[3]), c=float(r[4]), v=float(r[5]), di=i)
        else:
            cur["h"] = max(cur["h"], float(r[2]))
            cur["l"] = min(cur["l"], float(r[3]))
            cur["c"] = float(r[4])
            cur["v"] += float(r[5])
            cur["end"] = dates[i]
            cur["di"] = i
    # Final (possibly partial) block dropped — repaint guard.
    if len(bars) < 40:
        return 0
    df = pd.DataFrame({
        "open": [b["o"] for b in bars], "high": [b["h"] for b in bars],
        "low": [b["l"] for b in bars], "close": [b["c"] for b in bars],
        "volume": [b["v"] for b in bars],
    }, index=pd.DatetimeIndex([pd.Timestamp(b["end"]) for b in bars]))
    try:
        odf = compute_oscillator(df)
        wt1 = [None if pd.isna(v) else float(v) for v in odf["wt1"]]
        wt2 = [None if pd.isna(v) else float(v) for v in odf["wt2"]]
    except Exception as e:
        log.warning("[reddot] %s oscillator failed: %s", ticker,
                    str(e)[:300])
        return 0
    dots = find_red_dots(wt1, wt2)
    n = 0
    for bi in dots:
        di = bars[bi]["di"]
        d_date, px = dates[di], closes[di]
        lo2y = min(closes[max(0, di - 504):di + 1])
        ru = (px - lo2y) / lo2y if lo2y > 0 else 0.0
        f63 = round((closes[di + 63] / px - 1) * 100, 2) if di + 63 < len(closes) else None
        f126 = round((closes[di + 126] / px - 1) * 100, 2) if di + 126 < len(closes) else None
        f252 = round((closes[di + 252] / px - 1) * 100, 2) if di + 252 < len(closes) else None
        hi6 = max(closes[di + 1: di + 127]) if di + 1 < len(closes) else None
        dist_hi = round((hi6 / px - 1) * 100, 2) if hi6 is not None else None
        with conn.cursor() as c:
            c.execute("""INSERT INTO reddot_dots
                (ticker, dot_date, cross_height, runup_pct, ru_bucket,
                 px_at_dot, dist_to_high_pct, fwd_63d_pct, fwd_126d_pct,
                 fwd_252d_pct, era)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, dot_date) DO NOTHING""",
                (ticker, d_date, round(wt2[bi], 2), round(ru * 100, 1),
                 ru_bucket(ru), px, dist_hi, f63, f126, f252,
                 "pre2016" if d_date < dt.date(2016, 1, 1) else "post2016"))
        n += 1
    conn.commit()
    return n


def run(batch: int = 400) -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal_index = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT t.ticker FROM tickers t
                         WHERE COALESCE(t.delisted, false) = false
                           AND NOT EXISTS (SELECT 1 FROM reddot_progress p
                                           WHERE p.ticker = t.ticker)
                         ORDER BY t.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            log.info("[reddot] fleet complete.")
            return True
        for tk in todo:
            try:
                n = _process_ticker(conn, tk, cal_index)
            except Exception as e:
                conn.rollback()
                log.warning("[reddot] %s failed: %s", tk, str(e)[:300])
                n = 0
            with conn.cursor() as c:
                c.execute("""INSERT INTO reddot_progress (ticker, n_dots)
                             VALUES (%s,%s)
                             ON CONFLICT (ticker) DO NOTHING""", (tk, n))
            conn.commit()
        log.info("[reddot] processed %d ticker(s) this pass.", len(todo))
        return False
    finally:
        conn.close()
