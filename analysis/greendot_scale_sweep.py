"""
The block-size sweep (2026-08-29, Eric: "run a test to see if you find
a better time frame than the 16D... any time frame you think is the
best option. I want to see what you come up with vs what I have found
myself").

Same dot definition (find_dots imported), same drawdown condition,
same fixed-anchor no-repaint blocks, at block sizes 3, 8, 12, 21, 26,
32 trading days — filling the curve around the scales already graded
(daily=1, weekly≈5, 15, 16). Rows land in greendot_dots_ms under
scale='nd<k>' with the same fixed daily-horizon outcomes, so the
whole curve reads out of one table.

Pre-registered honesty rule for the readout: this sweep is
EXPLORATORY. A challenger block size beats 16D only if it wins in
BOTH eras and its neighboring sizes agree — a spike flanked by weaker
neighbors is grid noise (the marginal-dot flicker lesson), not a
discovery. Multiple comparisons are the whole risk of a sweep; the
curve renders in full or not at all.

compute_oscillator is the LIVE engine. Writes only greendot_dots_ms /
greendot_ms_progress. Resume per (scale, ticker).
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.greendot_sweep")

SWEEP_BLOCKS = (3, 8, 12, 21, 26, 32)
MIN_DAILY_ROWS = 700


def _process_ticker(conn, ticker, block, cal_index):
    import pandas as pd
    from analysis.greendot_robust15 import blocks_nd
    from analysis.greendot_study import bucket, find_dots
    from analysis.oscillator import compute_oscillator
    scale = f"nd{block}"
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
    blk = blocks_nd(dates, cal_index, block=block)
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
        log.warning("[greendot-sweep] %s %s oscillator failed: %s",
                    ticker, scale, str(e)[:300])
        return 0
    dots = find_dots(wt1, wt2)
    n = 0
    for bi in dots:
        di = bars[bi]["di"]
        d_date, px = dates[di], closes[di]
        hi2y = max(closes[max(0, di - 504):di + 1])
        dd = (hi2y - px) / hi2y if hi2y > 0 else 0.0
        f21 = round((closes[di + 21] / px - 1) * 100, 2) if di + 21 < len(closes) else None
        f63 = round((closes[di + 63] / px - 1) * 100, 2) if di + 63 < len(closes) else None
        f126 = round((closes[di + 126] / px - 1) * 100, 2) if di + 126 < len(closes) else None
        f252 = round((closes[di + 252] / px - 1) * 100, 2) if di + 252 < len(closes) else None
        low63 = min(closes[di + 1: di + 64]) if di + 1 < len(closes) else None
        dist63 = round((low63 / px - 1) * 100, 2) if low63 is not None else None
        with conn.cursor() as c:
            c.execute("""INSERT INTO greendot_dots_ms
                (scale, ticker, dot_date, cross_depth, drawdown_pct,
                 dd_bucket, px_at_dot, dist_low63_pct, fwd_21d_pct,
                 fwd_63d_pct, fwd_126d_pct, fwd_252d_pct, era)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (scale, ticker, dot_date) DO NOTHING""",
                (scale, ticker, d_date, round(wt2[bi], 2),
                 round(dd * 100, 1), bucket(dd), px, dist63,
                 f21, f63, f126, f252,
                 "pre2016" if d_date < dt.date(2016, 1, 1) else "post2016"))
        n += 1
    conn.commit()
    return n


def run(batch: int = 200) -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal_index = {r[0]: i for i, r in enumerate(c.fetchall())}
        for block in SWEEP_BLOCKS:
            scale = f"nd{block}"
            with conn.cursor() as c:
                c.execute("""SELECT t.ticker FROM tickers t
                             WHERE COALESCE(t.delisted, false) = false
                               AND NOT EXISTS
                                   (SELECT 1 FROM greendot_ms_progress p
                                    WHERE p.ticker = t.ticker
                                      AND p.scale = %s)
                             ORDER BY t.ticker LIMIT %s""",
                          (scale, batch))
                todo = [r[0] for r in c.fetchall()]
            if not todo:
                continue
            for tk in todo:
                try:
                    n = _process_ticker(conn, tk, block, cal_index)
                except Exception as e:
                    conn.rollback()
                    log.warning("[greendot-sweep] %s %s failed: %s",
                                scale, tk, str(e)[:300])
                    n = 0
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_ms_progress
                                 (scale, ticker, n_dots)
                                 VALUES (%s,%s,%s)
                                 ON CONFLICT (scale, ticker) DO NOTHING""",
                              (scale, tk, n))
                conn.commit()
            log.info("[greendot-sweep] %s: processed %d ticker(s).",
                     scale, len(todo))
            return False
        log.info("[greendot-sweep] all sweep scales complete.")
        return True
    finally:
        conn.close()
