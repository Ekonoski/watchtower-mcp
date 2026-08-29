"""
The green-dot ROBUSTNESS pass: same spec, 15-day blocks (2026-08-29,
Eric's 3-week question — a 3W calendar bar is 15 trading days, so if
the dot edge is real ~3-week structure it must survive re-blocking at
15; if it only exists at exactly 16 the finding is curve-fit and gets
trusted less. Pre-registered pass/fail: the deep-cohort (dd >= 50%,
cross <= -30) median 6-mo forward and %-positive must land in the same
neighborhood as the 16D read, era-split shown).

Everything else is the 16D study verbatim: fixed-anchor blocks on
SPY's calendar (no repaint), find_dots and bucket REUSED from
greendot_study, compute_oscillator is the LIVE engine. Writes only
greendot_dots15 / greendot15_progress. One-shot research pass with
resume; done when the fleet is exhausted.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.greendot15")

BLOCK = 15
MIN_DAILY_ROWS = 700


def blocks_nd(dates, cal_index, block=BLOCK):
    """Pure. Fixed-anchor block id per date: SPY-calendar index //
    block. Same inheritance rule for off-calendar dates as blocks_16d;
    block=16 reproduces it exactly (pinned by test)."""
    import bisect
    out = []
    keys = sorted(cal_index)
    for d in dates:
        i = cal_index.get(d)
        if i is None:
            j = bisect.bisect_right(keys, d) - 1
            i = cal_index[keys[j]] if j >= 0 else 0
        out.append(i // block)
    return out


def _process_ticker(conn, ticker, cal_index):
    import pandas as pd
    from analysis.greendot_study import bucket, find_dots
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
    blk = blocks_nd(dates, cal_index)
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
    # The final (possibly partial) block is dropped — repaint guard.
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
        log.warning("[greendot15] %s oscillator failed: %s", ticker,
                    str(e)[:300])
        return 0
    dots = find_dots(wt1, wt2)
    n = 0
    for bi in dots:
        di = bars[bi]["di"]
        d_date, px = dates[di], closes[di]
        hi2y = max(closes[max(0, di - 504):di + 1])
        dd = (hi2y - px) / hi2y if hi2y > 0 else 0.0
        f63 = round((closes[di + 63] / px - 1) * 100, 2) if di + 63 < len(closes) else None
        f126 = round((closes[di + 126] / px - 1) * 100, 2) if di + 126 < len(closes) else None
        f252 = round((closes[di + 252] / px - 1) * 100, 2) if di + 252 < len(closes) else None
        low6 = min(closes[di + 1: di + 127]) if di + 1 < len(closes) else None
        dist = round((low6 / px - 1) * 100, 2) if low6 is not None else None
        with conn.cursor() as c:
            c.execute("""INSERT INTO greendot_dots15
                (ticker, dot_date, cross_depth, drawdown_pct, dd_bucket,
                 px_at_dot, dist_to_low_pct, fwd_63d_pct, fwd_126d_pct,
                 fwd_252d_pct, era)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, dot_date) DO NOTHING""",
                (ticker, d_date, round(wt2[bi], 2), round(dd * 100, 1),
                 bucket(dd), px, dist, f63, f126, f252,
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
                           AND NOT EXISTS (SELECT 1 FROM greendot15_progress p
                                           WHERE p.ticker = t.ticker)
                         ORDER BY t.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            log.info("[greendot15] fleet complete.")
            return True
        for tk in todo:
            try:
                n = _process_ticker(conn, tk, cal_index)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot15] %s failed: %s", tk, str(e)[:300])
                n = 0
            with conn.cursor() as c:
                c.execute("""INSERT INTO greendot15_progress (ticker, n_dots)
                             VALUES (%s,%s)
                             ON CONFLICT (ticker) DO NOTHING""", (tk, n))
            conn.commit()
        log.info("[greendot15] processed %d ticker(s) this pass.", len(todo))
        return False
    finally:
        conn.close()
