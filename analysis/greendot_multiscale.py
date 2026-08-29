"""
The multiscale dot study (2026-08-29, Eric: "is it better to get
smaller moves on a daily bounce or weekly bounce... or hold longer
with the 16d dot? Regarding increasing my account faster?" — and his
correction that this is HIS cipher dip-buying at three clock speeds,
not the paper desk's pattern book).

Pre-registered, frozen before any number: the SAME dot definition the
16D study graded — wavetrend cross-up with the cross below zero
(find_dots, imported never reimplemented) — computed on DAILY bars
and on completed ISO-WEEK bars, same drawdown condition vs the
trailing 2-year high, same depth throttle available at readout.
Outcomes at FIXED daily horizons for every scale (21/63/126/252
trading days from the signal bar's real daily close) so per-scale
compounding rates compare on one ruler; dist to the fwd 63-day low
rides along as the drawdown metric. The 16D record already exists
(greendot_dots) and is NOT recomputed here.

compute_oscillator is the LIVE engine. Writes only greendot_dots_ms /
greendot_ms_progress. One-shot research pass with resume per scale.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.greendot_ms")

SCALES = ("daily", "weekly")
MIN_DAILY_ROWS = 700


def week_bars(dates, closes, opens, highs, lows):
    """Pure. Completed ISO-week bars; the in-progress final week is
    dropped (a half-week bar is tomorrow's repaint)."""
    out, cur_key = [], None
    for i, d in enumerate(dates):
        key = d.isocalendar()[:2]
        if key != cur_key:
            out.append(dict(o=opens[i], h=highs[i], l=lows[i],
                            c=closes[i], di=i))
            cur_key = key
        else:
            out[-1]["h"] = max(out[-1]["h"], highs[i])
            out[-1]["l"] = min(out[-1]["l"], lows[i])
            out[-1]["c"] = closes[i]
            out[-1]["di"] = i
    return out[:-1]


def _dots_for(df_bars, ticker, scale):
    import pandas as pd
    from analysis.greendot_study import find_dots
    from analysis.oscillator import compute_oscillator
    try:
        odf = compute_oscillator(df_bars)
        wt1 = [None if pd.isna(v) else float(v) for v in odf["wt1"]]
        wt2 = [None if pd.isna(v) else float(v) for v in odf["wt2"]]
    except Exception as e:
        log.warning("[greendot-ms] %s %s oscillator failed: %s",
                    ticker, scale, str(e)[:300])
        return [], []
    return find_dots(wt1, wt2), wt2


def _process_ticker(conn, ticker, scale):
    import pandas as pd
    from analysis.greendot_study import bucket
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
    opens = [float(r[1]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    vols = [float(r[5]) for r in rows]
    if scale == "daily":
        bars = [dict(o=opens[i], h=highs[i], l=lows[i], c=closes[i],
                     di=i) for i in range(len(dates))]
        vseq = vols
        idx = [pd.Timestamp(d) for d in dates]
    else:
        bars = week_bars(dates, closes, opens, highs, lows)
        vseq = [0.0] * len(bars)
        idx = [pd.Timestamp(dates[b["di"]]) for b in bars]
    if len(bars) < 60:
        return 0
    df = pd.DataFrame({
        "open": [b["o"] for b in bars], "high": [b["h"] for b in bars],
        "low": [b["l"] for b in bars], "close": [b["c"] for b in bars],
        "volume": vseq,
    }, index=pd.DatetimeIndex(idx))
    dots, wt2 = _dots_for(df, ticker, scale)
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
        for scale in SCALES:
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
                    n = _process_ticker(conn, tk, scale)
                except Exception as e:
                    conn.rollback()
                    log.warning("[greendot-ms] %s %s failed: %s",
                                scale, tk, str(e)[:300])
                    n = 0
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_ms_progress
                                 (scale, ticker, n_dots)
                                 VALUES (%s,%s,%s)
                                 ON CONFLICT (scale, ticker) DO NOTHING""",
                              (scale, tk, n))
                conn.commit()
            log.info("[greendot-ms] %s: processed %d ticker(s).",
                     scale, len(todo))
            return False
        log.info("[greendot-ms] all scales complete.")
        return True
    finally:
        conn.close()
