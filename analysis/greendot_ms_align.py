"""
The daily-dot ALIGNMENT pass (2026-08-29, Eric's correction of the
multiscale read: "it's not a daily green dot taken... the price moving
above the 8/21 on the daily before they flip. Every daily green dot is
probably not even profitable. The stocks have to align bullishly").

The raw daily dot is the wrong object — his daily trade is the dot
PLUS alignment. Pre-registered, per deep daily dot in greendot_dots_ms:

  state at the dot bar   close vs the 8- and 21-day EMAs, and whether
                         the EMAs themselves have crossed (8 > 21) —
                         "before they flip" means price above both
                         while the EMAs are still inverted.
  the entry              if price is not already above both at the
                         dot: first daily close within 15 trading
                         days clearing BOTH EMAs (his no-cross rule
                         at daily speed); entry at that real close.
  outcomes               fwd 21/63/126 trading days from the ENTRY,
                         MAE over the next 21 days, premium over the
                         dot close. A dot that never clears inside
                         the window records no_clear.

Writes only greendot_ms_align; ema() reused from greendot_ema_entry.
Runs behind the multiscale base pass — processes whatever dots exist,
finishes as the base pass finishes (no marker; an empty todo is done
for this boot).
"""
import logging

log = logging.getLogger("watchtower.greendot_align")

CLEAR_WINDOW = 15          # trading days after the dot to clear the 8/21
CLEAR_WINDOW_W = 15        # weekly variant: 15 WEEKLY bars (~15 weeks) —
                           # the same 15-bars-of-native-scale patience,
                           # pre-registered for symmetry with the daily


def run(batch: int = 400) -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT DISTINCT m.ticker FROM greendot_dots_ms m
                         WHERE m.scale='daily'
                           AND NOT EXISTS (SELECT 1 FROM greendot_ms_align a
                                           WHERE a.ms_id = m.id)
                         ORDER BY m.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            log.info("[greendot-align] nothing pending.")
            return True
        for tk in todo:
            try:
                _one_ticker(conn, tk)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot-align] %s failed: %s", tk, str(e)[:300])
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_ms_align (ms_id, note)
                                 SELECT id, 'ticker_error'
                                 FROM greendot_dots_ms
                                 WHERE scale='daily' AND ticker=%s
                                 ON CONFLICT DO NOTHING""", (tk,))
                conn.commit()
        log.info("[greendot-align] processed %d ticker(s).", len(todo))
        return False
    finally:
        conn.close()


def run_weekly(batch: int = 400) -> bool:
    """The weekly alignment pass (2026-08-29, Eric: 'now let's run the
    weekly on the price move above the 8/21 ema like we did with the
    daily time frame'). Same rule at weekly speed: state vs the WEEKLY
    8/21 EMAs at the dot; entry on the first weekly close clearing
    both within 15 weekly bars; outcomes from the entry's real close.
    Rows land in the same greendot_ms_align table."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT DISTINCT m.ticker FROM greendot_dots_ms m
                         WHERE m.scale='weekly'
                           AND NOT EXISTS (SELECT 1 FROM greendot_ms_align a
                                           WHERE a.ms_id = m.id)
                         ORDER BY m.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            log.info("[greendot-align] weekly: nothing pending.")
            return True
        for tk in todo:
            try:
                _one_ticker_weekly(conn, tk)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot-align] weekly %s failed: %s",
                            tk, str(e)[:300])
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_ms_align (ms_id, note)
                                 SELECT id, 'ticker_error'
                                 FROM greendot_dots_ms
                                 WHERE scale='weekly' AND ticker=%s
                                 ON CONFLICT DO NOTHING""", (tk,))
                conn.commit()
        log.info("[greendot-align] weekly processed %d ticker(s).", len(todo))
        return False
    finally:
        conn.close()


def _one_ticker_weekly(conn, tk):
    import bisect
    from analysis.greendot_ema_entry import (ema, find_above_both,
                                             week_end_indices)
    with conn.cursor() as c:
        c.execute("""SELECT id, dot_date FROM greendot_dots_ms
                     WHERE scale='weekly' AND ticker=%s
                     ORDER BY dot_date""", (tk,))
        dots = c.fetchall()
        c.execute("""SELECT trade_date, close FROM daily_prices
                     WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (tk,))
        rows = c.fetchall()
    if not dots or len(rows) < 60:
        return
    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    wk_end = week_end_indices(dates)
    wk_c = [closes[e] for e in wk_end]
    e8, e21 = ema(wk_c, 8), ema(wk_c, 21)
    date_to_di = {d: i for i, d in enumerate(dates)}
    for mid, d0 in dots:
        di0 = date_to_di.get(d0)
        if di0 is None:
            continue
        w0 = bisect.bisect_right(wk_end, di0) - 1   # the dot's week
        if w0 < 0 or w0 >= len(wk_c):
            continue
        above8 = wk_c[w0] > e8[w0]
        above21 = wk_c[w0] > e21[w0]
        ema_crossed = e8[w0] > e21[w0]
        if above8 and above21:
            wi = w0
        else:
            wi = find_above_both(wk_c, e8, e21, w0,
                                 min(w0 + CLEAR_WINDOW_W, len(wk_c) - 1))
        with conn.cursor() as c:
            if wi is None:
                c.execute("""INSERT INTO greendot_ms_align
                    (ms_id, above8_at_dot, above21_at_dot,
                     ema_crossed_at_dot, cleared, note)
                    VALUES (%s,%s,%s,%s,false,'no_clear')
                    ON CONFLICT DO NOTHING""",
                    (mid, above8, above21, ema_crossed))
                continue
            edi = wk_end[wi] if wi != w0 else di0
            epx = closes[edi]
            prem = round((epx / closes[di0] - 1) * 100, 2)
            seg = closes[edi + 1: edi + 22]
            mae21 = round((min(seg) / epx - 1) * 100, 2) if seg else None
            f21 = round((closes[edi + 21] / epx - 1) * 100, 2) \
                if edi + 21 < len(closes) else None
            f63 = round((closes[edi + 63] / epx - 1) * 100, 2) \
                if edi + 63 < len(closes) else None
            f126 = round((closes[edi + 126] / epx - 1) * 100, 2) \
                if edi + 126 < len(closes) else None
            c.execute("""INSERT INTO greendot_ms_align
                (ms_id, above8_at_dot, above21_at_dot, ema_crossed_at_dot,
                 cleared, entry_date, entry_px, premium_pct, mae21_pct,
                 fwd_21d_pct, fwd_63d_pct, fwd_126d_pct)
                VALUES (%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""",
                (mid, above8, above21, ema_crossed, dates[edi],
                 round(epx, 4), prem, mae21, f21, f63, f126))
    conn.commit()


def _one_ticker(conn, tk):
    from analysis.greendot_ema_entry import ema, find_above_both
    with conn.cursor() as c:
        c.execute("""SELECT id, dot_date FROM greendot_dots_ms
                     WHERE scale='daily' AND ticker=%s
                     ORDER BY dot_date""", (tk,))
        dots = c.fetchall()
        c.execute("""SELECT trade_date, close FROM daily_prices
                     WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (tk,))
        rows = c.fetchall()
    if not dots or len(rows) < 60:
        return
    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    e8, e21 = ema(closes, 8), ema(closes, 21)
    date_to_di = {d: i for i, d in enumerate(dates)}
    for mid, d0 in dots:
        di0 = date_to_di.get(d0)
        if di0 is None:
            continue
        above8 = closes[di0] > e8[di0]
        above21 = closes[di0] > e21[di0]
        ema_crossed = e8[di0] > e21[di0]
        if above8 and above21:
            edi = di0                     # already aligned at the dot
        else:
            edi = find_above_both(closes, e8, e21, di0,
                                  min(di0 + CLEAR_WINDOW, len(closes) - 1))
        with conn.cursor() as c:
            if edi is None:
                c.execute("""INSERT INTO greendot_ms_align
                    (ms_id, above8_at_dot, above21_at_dot, ema_crossed_at_dot,
                     cleared, note)
                    VALUES (%s,%s,%s,%s,false,'no_clear')
                    ON CONFLICT DO NOTHING""",
                    (mid, above8, above21, ema_crossed))
                continue
            epx = closes[edi]
            prem = round((epx / closes[di0] - 1) * 100, 2)
            seg = closes[edi + 1: edi + 22]
            mae21 = round((min(seg) / epx - 1) * 100, 2) if seg else None
            f21 = round((closes[edi + 21] / epx - 1) * 100, 2) \
                if edi + 21 < len(closes) else None
            f63 = round((closes[edi + 63] / epx - 1) * 100, 2) \
                if edi + 63 < len(closes) else None
            f126 = round((closes[edi + 126] / epx - 1) * 100, 2) \
                if edi + 126 < len(closes) else None
            c.execute("""INSERT INTO greendot_ms_align
                (ms_id, above8_at_dot, above21_at_dot, ema_crossed_at_dot,
                 cleared, entry_date, entry_px, premium_pct, mae21_pct,
                 fwd_21d_pct, fwd_63d_pct, fwd_126d_pct)
                VALUES (%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""",
                (mid, above8, above21, ema_crossed, dates[edi],
                 round(epx, 4), prem, mae21, f21, f63, f126))
    conn.commit()
