"""
The wall-touch prior (2026-09-02, pre-registered — Eric, on the 📍
alert: "did that alert come out because we were most likely going to
come to that level?" It didn't; it fires on arrival. This study makes
the OTHER question answerable: given the morning board, how often does
each level actually get touched before the close?)

SPEC (frozen before any number):
  event      the day's FIRST intraday board per ticker (earliest
             gex_intraday row, ~9:35 ET) — spot, call wall, put wall,
             flip, regime. The 7:30 sweep row is overwritten intraday,
             so the 9:35 board is the earliest board the record keeps.
  levels     call_wall / put_wall / gamma_flip within +/-3% of spot.
  condition  signed distance (spot - level)/level bucketed
             <0.25 / 0.25-0.5 / 0.5-1 / 1-2 / 2-3 %, regime, level kind.
  outcome    touched = a completed RTH bar after the board with
             low <= level <= high, by the close; first touch time;
             touched_1h = within 60 minutes of the board.
  bars       SPY/QQQ/IWM/DIA from index_intraday_bars (15m; IWM holey,
             DIA may be absent), mega-caps from mag7_1m_bars (1m).
             Missing bars = touched NULL = a HOLE, never "not touched".
  baseline   stated where the numbers surface: the record starts
             2026-08-19 (11 sessions at build) — small-n everywhere,
             n printed beside every percentage, no era split possible
             yet; the table grows by one row per level per ticker per
             session and the prior sharpens on its own.
Writes ONLY wall_touch_events. Idempotent by (ticker, trade_date, kind).
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.wall_touch")

KINDS = (("call_wall", "call_wall"), ("put_wall", "put_wall"),
         ("gamma_flip", "gamma_flip"))
MAX_DIST_PCT = 3.0
BUCKETS = ((0.0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 3.0))


def bucket(dist_pct):
    d = abs(float(dist_pct))
    for lo, hi in BUCKETS:
        if lo <= d < hi:
            return (lo, hi)
    return None


def touch_outcome(bars, level, board_ts):
    """Pure. bars = [(ts, o, h, l, c)] oldest first (any grain); the
    first bar strictly after board_ts whose range contains level is
    the touch. Returns (touched, touch_ts, touched_1h); (None, None,
    None) when there are no bars after the board — a hole."""
    after = [b for b in bars if b[0] > board_ts]
    if not after:
        return None, None, None
    level = float(level)
    for ts, o, h, l, c in after:
        if float(l) <= level <= float(h):
            return True, ts, (ts - board_ts) <= dt.timedelta(hours=1)
    return False, None, False


def _bars_for(conn, ticker, day):
    from analysis.gex import INDEXES
    if ticker in INDEXES:
        table, src = "index_intraday_bars", "index_15m"
    else:
        table, src = "mag7_1m_bars", "mag7_1m"
    with conn.cursor() as c:
        c.execute(f"""SELECT ts, open, high, low, close FROM {table}
                      WHERE ticker=%s AND trade_date=%s ORDER BY ts""",
                  (ticker, day))
        rows = c.fetchall()
    return rows, (src if rows else "none")


def grade_day(conn, day) -> int:
    """Grade every drift ticker's first board of `day`. Returns rows
    written (holes count — they are rows with touched NULL)."""
    from analysis.gex import DRIFT_TICKERS
    n = 0
    for tk in DRIFT_TICKERS:
        with conn.cursor() as c:
            c.execute("""SELECT ts, spot, call_wall, put_wall, gamma_flip,
                                regime
                         FROM gex_intraday
                         WHERE ticker=%s AND ts::date=%s
                         ORDER BY ts ASC LIMIT 1""", (tk, day))
            r = c.fetchone()
        if not r or r[1] is None:
            continue
        board_ts, spot, cw, pw, flip, regime = r
        spot = float(spot)
        bars, src = _bars_for(conn, tk, day)
        for kind, _ in KINDS:
            level = {"call_wall": cw, "put_wall": pw,
                     "gamma_flip": flip}[kind]
            if level is None or float(level) <= 0:
                continue
            dist = (spot - float(level)) / float(level) * 100.0
            if abs(dist) > MAX_DIST_PCT:
                continue
            touched, touch_ts, t1h = touch_outcome(bars, level, board_ts)
            with conn.cursor() as c:
                c.execute("""INSERT INTO wall_touch_events
                    (ticker, trade_date, kind, board_ts, level, spot,
                     dist_pct, regime, touched, touch_ts, touched_1h,
                     bars_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ticker, trade_date, kind) DO UPDATE SET
                      touched=EXCLUDED.touched, touch_ts=EXCLUDED.touch_ts,
                      touched_1h=EXCLUDED.touched_1h,
                      bars_source=EXCLUDED.bars_source""",
                          (tk, day, kind, board_ts, float(level), spot,
                           round(dist, 3), regime, touched, touch_ts, t1h,
                           src))
            n += 1
    conn.commit()
    return n


def run(day=None) -> dict:
    """Nightly (16:50) for today, or backfill every day the intraday
    board record covers when day is None."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        if day is not None:
            days = [day]
        else:
            with conn.cursor() as c:
                c.execute("SELECT DISTINCT ts::date FROM gex_intraday "
                          "ORDER BY 1")
                days = [r[0] for r in c.fetchall()]
        total = 0
        for d in days:
            total += grade_day(conn, d)
        log.info("[wall-touch] graded %d level-days across %d day(s).",
                 total, len(days))
        return {"rows": total, "days": len(days)}
    finally:
        conn.close()


def prior(conn, kind, regime, dist_pct):
    """(pct_touched, n, n_holes) for the bucket of dist_pct, same kind
    and regime. n counts graded rows only; holes are reported, never
    folded into the denominator. None when the bucket is empty."""
    b = bucket(dist_pct)
    if b is None:
        return None
    with conn.cursor() as c:
        c.execute("""SELECT count(*) FILTER (WHERE touched IS NOT NULL),
                            count(*) FILTER (WHERE touched),
                            count(*) FILTER (WHERE touched IS NULL)
                     FROM wall_touch_events
                     WHERE kind=%s AND regime IS NOT DISTINCT FROM %s
                       AND abs(dist_pct) >= %s AND abs(dist_pct) < %s""",
                  (kind, regime, b[0], b[1]))
        n, hit, holes = c.fetchone()
    if not n:
        return None
    return round(100.0 * hit / n, 0), n, holes
