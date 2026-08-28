"""
The 16D below-zero green-dot study (2026-08-28, Eric: "the 16 day
market cipher is basically the GOAT of mid to long term holds... every
green dot indicates the bottom, or the bottom within 3-6 months" —
refined twice the same session: dots BELOW THE ZERO LINE, on stocks in
MAJOR DRAWDOWN. VFF-16D is the named archetype).

Pre-registered spec (frozen before any number was computed):
  event      16D wavetrend cross-up with the cross below zero
  condition  drawdown vs trailing 2-year high, cut by bucket
             (<30 / 30-50 / 50-70 / 70%+) — all dots kept, buckets compare
  outcomes   distance to the forward 6-month low, whether a LOWER dot
             followed within a year (first-dot vs later-dot bottoms),
             3/6/12-month forward returns
  baselines  computed at readout: random days on the same drawdown
             cohort; era split pre/post-2016; survivorship stated
             (currently-listed universe — the corpses' dots are unseen)

Bar construction — the part that has bitten before: an END-anchored
k-day resample repaints every session (the BW-3D lesson, in CLAUDE.md).
Bars here are FIXED-ANCHOR: every date maps to its index in SPY's own
trading-day calendar (complete, never back-extended), and 16-day blocks
are cut on that absolute grid. Yesterday's dot can never move.

Runs as a boot seeder (resume via greendot_progress; done when every
fleet ticker is processed). Reads daily_prices, writes only its own
tables. compute_oscillator is the LIVE engine — no reimplementation.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.greendot")

BLOCK = 16
MIN_DAILY_ROWS = 700          # ~2y trailing high + enough bars to matter
DD_BUCKETS = ((0.70, "gte70"), (0.50, "b50_70"), (0.30, "b30_50"),
              (-1.0, "lt30"))


def bucket(dd):
    for thresh, name in DD_BUCKETS:
        if dd >= thresh:
            return name
    return "lt30"


def blocks_16d(dates, cal_index):
    """Pure. Fixed-anchor block id per date: SPY-calendar index // 16.
    Dates missing from the calendar (holidays mismatch, foreign
    listings) inherit the previous calendar date's index."""
    out = []
    keys = sorted(cal_index)
    import bisect
    for d in dates:
        i = cal_index.get(d)
        if i is None:
            j = bisect.bisect_right(keys, d) - 1
            i = cal_index[keys[j]] if j >= 0 else 0
        out.append(i // BLOCK)
    return out


def find_dots(wt1, wt2):
    """Pure. Indices of wavetrend cross-ups where the cross is below
    zero (wt2 at the cross bar ≤ 0) — Eric's zero-line leg."""
    idx = []
    for i in range(1, len(wt1)):
        a1, a2, b1, b2 = wt1[i - 1], wt2[i - 1], wt1[i], wt2[i]
        if None in (a1, a2, b1, b2):
            continue
        if a1 <= a2 and b1 > b2 and b2 <= 0:
            idx.append(i)
    return idx


def _process_ticker(conn, ticker, cal_index):
    import pandas as pd
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
    # Aggregate to fixed-anchor 16D bars; drop the (possibly partial)
    # final block — an incomplete bar is tomorrow's repaint.
    bars, cur, cur_id = [], None, None
    for i, r in enumerate(rows):
        if blk[i] != cur_id:
            if cur is not None:
                bars.append(cur)
            cur_id = blk[i]
            cur = dict(end=dates[i], o=float(r[1]), h=float(r[2]),
                       l=float(r[3]), c=float(r[4]), v=float(r[5]),
                       di=i)
        else:
            cur["h"] = max(cur["h"], float(r[2]))
            cur["l"] = min(cur["l"], float(r[3]))
            cur["c"] = float(r[4])
            cur["v"] += float(r[5])
            cur["end"] = dates[i]
            cur["di"] = i
    # cur (the last, possibly-partial block) is deliberately dropped.
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
        log.warning("[greendot] %s oscillator failed: %s", ticker,
                    str(e)[:300])
        return 0
    dots = find_dots(wt1, wt2)
    n = 0
    for bi in dots:
        di = bars[bi]["di"]              # daily index of the dot bar's end
        d_date, px = dates[di], closes[di]
        hi2y = max(closes[max(0, di - 504):di + 1])
        dd = (hi2y - px) / hi2y if hi2y > 0 else 0.0
        fwd = closes[di + 1: di + 253]
        f63 = round((closes[di + 63] / px - 1) * 100, 2) if di + 63 < len(closes) else None
        f126 = round((closes[di + 126] / px - 1) * 100, 2) if di + 126 < len(closes) else None
        f252 = round((closes[di + 252] / px - 1) * 100, 2) if di + 252 < len(closes) else None
        low6 = min(closes[di + 1: di + 127]) if di + 1 < len(closes) else None
        dist = round((low6 / px - 1) * 100, 2) if low6 is not None else None
        later_lower = any(closes[bars[bj]["di"]] < px for bj in dots
                          if bi < bj and bars[bj]["di"] <= di + 252)
        with conn.cursor() as c:
            c.execute("""INSERT INTO greendot_dots
                (ticker, dot_date, cross_depth, drawdown_pct, dd_bucket,
                 px_at_dot, fwd_low_6m, dist_to_low_pct,
                 lower_dot_followed, fwd_63d_pct, fwd_126d_pct,
                 fwd_252d_pct, era)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, dot_date) DO NOTHING""",
                (ticker, d_date, round(wt2[bi], 2), round(dd * 100, 1),
                 bucket(dd), px, low6, dist, later_lower, f63, f126, f252,
                 "pre2016" if d_date < dt.date(2016, 1, 1) else "post2016"))
        n += 1
    conn.commit()
    return n


def _ensure_vff(conn):
    """The archetype must not be a hole: seed VFF's ticker row and its
    daily history (research fetch) before the fleet pass."""
    from analysis.polygon_data import fetch_recent_bars
    with conn.cursor() as c:
        c.execute("SELECT count(*) FROM daily_prices WHERE ticker='VFF'")
        have = c.fetchone()[0]
    if have > 900:
        return
    rows = fetch_recent_bars("VFF", days=1600, multiplier=1, timespan="day")
    with conn.cursor() as c:
        c.execute("""INSERT INTO tickers (ticker, company_name, sector,
                                          exchange)
                     VALUES ('VFF','Village Farms International',
                             'Consumer Defensive','NASDAQ')
                     ON CONFLICT (ticker) DO NOTHING""")
        for b in rows:
            c.execute("""INSERT INTO daily_prices
                         (ticker, trade_date, open, high, low, close, volume)
                         VALUES ('VFF',%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (ticker, trade_date) DO NOTHING""",
                      (b["date"], b["open"], b["high"], b["low"],
                       b["close"], b.get("volume")))
    conn.commit()
    log.info("[greendot] VFF onboarded: %d daily bars ensured.", len(rows))


def run(batch: int = 400) -> bool:
    """Process up to `batch` unprocessed fleet tickers; True when the
    whole fleet is done (marker via empty todo)."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        _ensure_vff(conn)
        with conn.cursor() as c:
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal_index = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT t.ticker FROM tickers t
                         WHERE COALESCE(t.delisted, false) = false
                           AND NOT EXISTS (SELECT 1 FROM greendot_progress p
                                           WHERE p.ticker = t.ticker)
                         ORDER BY t.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            log.info("[greendot] fleet complete.")
            return True
        for tk in todo:
            try:
                n = _process_ticker(conn, tk, cal_index)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot] %s failed: %s", tk, str(e)[:300])
                n = 0
            with conn.cursor() as c:
                c.execute("""INSERT INTO greendot_progress (ticker, n_dots)
                             VALUES (%s,%s)
                             ON CONFLICT (ticker) DO NOTHING""", (tk, n))
            conn.commit()
        log.info("[greendot] processed %d ticker(s) this pass.", len(todo))
        return False
    finally:
        conn.close()
