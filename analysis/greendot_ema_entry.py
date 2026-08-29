"""
The green-dot EMA-reclaim entry variants (2026-08-29, Eric: "wait for
a green dot and price to move above the 8/21 ema. Not for the ema's to
cross, just price to move above them" — the goal stated with it: avoid
the drawdown without losing too much profit).

Pre-registered, frozen before any number. Two sub-variants because
"the 8/21" is ambiguous between chart timeframes — both are graded,
neither is assumed:

  ema_daily  first DAILY close after the dot above BOTH the 8- and
             21-day EMAs of daily closes; enter at that close.
  ema_16d    first completed 16D bar (fixed-anchor, the study's own
             grid) whose close sits above BOTH the 8- and 21-bar EMAs
             of 16D closes; enter at that bar's end-date real close.
  ema_weekly the same rule on completed CALENDAR-WEEK bars (ISO
             weeks; the in-progress week never counts) — added the
             same day at Eric's follow-up ("any better on the
             weekly?"); its own resume/marker so it backfills tickers
             the first pass already processed.

No cross requirement — the EMAs may still be inverted (8 under 21);
price above both is the whole trigger. Same 8-block (~6-mo) window,
same named misses, same outcomes (MAE after entry, fwd 6/12-mo from
entry) as the entry-schedule study; rows land in the SAME
greendot_entry table under the new variant names so every entry
method ever graded reads out of one place. Writes only that table.
One-shot with resume; a ticker error leaves hole rows, never a loop.
"""
import logging

log = logging.getLogger("watchtower.greendot_ema")

COMPLETE_MARKER = "greendot_ema_v1"
WEEKLY_MARKER = "greendot_ema_w_v1"
VARIANTS = ("ema_daily", "ema_16d", "ema_weekly")


def week_end_indices(dates):
    """Pure. Index of each ISO week's last trading day, completed
    weeks only — the final (in-progress or last-known) week is
    excluded, so a half-week close can never trigger."""
    out, cur_key = [], None
    for i, d in enumerate(dates):
        key = d.isocalendar()[:2]
        if key != cur_key:
            out.append(i)
            cur_key = key
        else:
            out[-1] = i
    return out[:-1] if out else []


def ema(values, n):
    """Pure. Standard EMA, alpha=2/(n+1), seeded at the first value."""
    if not values:
        return []
    k = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def find_above_both(closes, e8, e21, start, end):
    """Pure. First index in (start, end] where close > ema8 AND
    close > ema21. No cross condition — price above both is all."""
    for i in range(start + 1, min(end + 1, len(closes))):
        if closes[i] > e8[i] and closes[i] > e21[i]:
            return i
    return None


def run(batch: int = 400) -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT DISTINCT g.ticker FROM greendot_dots g
                         WHERE NOT EXISTS (SELECT 1 FROM greendot_entry e
                                           WHERE e.dot_id = g.id
                                             AND e.variant = 'ema_daily')
                         ORDER BY g.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                          "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                          (COMPLETE_MARKER,))
            conn.commit()
            log.info("[greendot-ema] complete.")
            return True
        for tk in todo:
            try:
                _one_ticker(conn, tk, cal)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot-ema] %s failed: %s", tk, str(e)[:300])
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_entry
                                 (dot_id, variant, entered, note)
                                 SELECT id, 'ema_daily', false, 'ticker_error'
                                 FROM greendot_dots WHERE ticker=%s
                                 ON CONFLICT DO NOTHING""", (tk,))
                conn.commit()
        log.info("[greendot-ema] processed %d ticker(s).", len(todo))
        return False
    finally:
        conn.close()


def run_weekly(batch: int = 400) -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (WEEKLY_MARKER,))
            if c.fetchone():
                return True
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT DISTINCT g.ticker FROM greendot_dots g
                         WHERE NOT EXISTS (SELECT 1 FROM greendot_entry e
                                           WHERE e.dot_id = g.id
                                             AND e.variant = 'ema_weekly')
                         ORDER BY g.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                          "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                          (WEEKLY_MARKER,))
            conn.commit()
            log.info("[greendot-ema] weekly complete.")
            return True
        for tk in todo:
            try:
                _one_ticker_weekly(conn, tk, cal)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot-ema] weekly %s failed: %s",
                            tk, str(e)[:300])
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_entry
                                 (dot_id, variant, entered, note)
                                 SELECT id, 'ema_weekly', false, 'ticker_error'
                                 FROM greendot_dots WHERE ticker=%s
                                 ON CONFLICT DO NOTHING""", (tk,))
                conn.commit()
        log.info("[greendot-ema] weekly processed %d ticker(s).", len(todo))
        return False
    finally:
        conn.close()


def _one_ticker_weekly(conn, tk, cal):
    import bisect
    from analysis.greendot_entry_study import WINDOW_BLOCKS, _miss
    from analysis.greendot_study import blocks_16d
    with conn.cursor() as c:
        c.execute("""SELECT id, dot_date, px_at_dot FROM greendot_dots
                     WHERE ticker=%s ORDER BY dot_date""", (tk,))
        dots = c.fetchall()
        c.execute("""SELECT trade_date, close FROM daily_prices
                     WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (tk,))
        rows = c.fetchall()
    if not dots or len(rows) < 100:
        return
    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    wk_end = week_end_indices(dates)
    wk_c = [closes[e] for e in wk_end]
    e8w, e21w = ema(wk_c, 8), ema(wk_c, 21)
    blk = blocks_16d(dates, cal)
    # 16D block ends only to bound the window identically to the
    # other variants — the trigger itself reads weekly bars.
    bar_end_di, cur_id = [], None
    for i in range(len(dates)):
        if blk[i] != cur_id:
            cur_id = blk[i]
            bar_end_di.append(i)
        else:
            bar_end_di[-1] = i
    date_to_di = {d: i for i, d in enumerate(dates)}
    daily_for_miss = [dict(c=c) for c in closes]

    def outcomes(entry_di, entry_px):
        seg = closes[entry_di + 1: entry_di + 127]
        mae = round((min(seg) / entry_px - 1) * 100, 2) if seg else None
        f6 = round((closes[entry_di + 126] / entry_px - 1) * 100, 2) \
            if entry_di + 126 < len(closes) else None
        f12 = round((closes[entry_di + 252] / entry_px - 1) * 100, 2) \
            if entry_di + 252 < len(closes) else None
        return mae, f6, f12

    for did, d0, px0 in dots:
        px0 = float(px0)
        di0 = date_to_di.get(d0)
        if di0 is None:
            continue
        b0 = next((bi for bi, e in enumerate(bar_end_di) if e >= di0), None)
        if b0 is None:
            continue
        b_end = b0 + WINDOW_BLOCKS
        win_end_di = min(bar_end_di[b_end] if b_end < len(bar_end_di)
                         else len(closes) - 1, len(closes) - 1)
        w0 = bisect.bisect_right(wk_end, di0) - 1    # last week ending ≤ dot
        w_last = bisect.bisect_right(wk_end, win_end_di) - 1
        wi = find_above_both(wk_c, e8w, e21w, w0, w_last)
        with conn.cursor() as c:
            if wi is None:
                c.execute("""INSERT INTO greendot_entry
                    (dot_id, variant, entered, note)
                    VALUES (%s,'ema_weekly',false,%s)
                    ON CONFLICT DO NOTHING""",
                    (did, _miss(daily_for_miss, di0, win_end_di, px0)))
            else:
                edi = wk_end[wi]
                epx = closes[edi]
                mae, f6, f12 = outcomes(edi, epx)
                c.execute("""INSERT INTO greendot_entry
                    (dot_id, variant, entered, entry_date, entry_px,
                     deployed_frac, mae_pct, fwd6m_pct, fwd12m_pct)
                    VALUES (%s,'ema_weekly',true,%s,%s,1.0,%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (did, dates[edi], round(epx, 4), mae, f6, f12))
    conn.commit()


def _one_ticker(conn, tk, cal):
    from analysis.greendot_entry_study import WINDOW_BLOCKS, _miss
    from analysis.greendot_study import blocks_16d
    with conn.cursor() as c:
        c.execute("""SELECT id, dot_date, px_at_dot FROM greendot_dots
                     WHERE ticker=%s ORDER BY dot_date""", (tk,))
        dots = c.fetchall()
        c.execute("""SELECT trade_date, close FROM daily_prices
                     WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (tk,))
        rows = c.fetchall()
    if not dots or len(rows) < 100:
        return
    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    e8d, e21d = ema(closes, 8), ema(closes, 21)
    blk = blocks_16d(dates, cal)
    bar_c, bar_end_di, cur_id = [], [], None
    for i, cl in enumerate(closes):
        if blk[i] != cur_id:
            if cur_id is not None:
                bar_c.append(closes[bar_end_di[-1]])
            cur_id = blk[i]
            bar_end_di.append(i)
        else:
            bar_end_di[-1] = i
    if bar_end_di:
        bar_c.append(closes[bar_end_di[-1]])
    e8b, e21b = ema(bar_c, 8), ema(bar_c, 21)
    date_to_di = {d: i for i, d in enumerate(dates)}

    def outcomes(entry_di, entry_px):
        seg = [c for c in closes[entry_di + 1: entry_di + 127]]
        mae = round((min(seg) / entry_px - 1) * 100, 2) if seg else None
        f6 = round((closes[entry_di + 126] / entry_px - 1) * 100, 2) \
            if entry_di + 126 < len(closes) else None
        f12 = round((closes[entry_di + 252] / entry_px - 1) * 100, 2) \
            if entry_di + 252 < len(closes) else None
        return mae, f6, f12

    daily_for_miss = [dict(c=c) for c in closes]
    for did, d0, px0 in dots:
        px0 = float(px0)
        di0 = date_to_di.get(d0)
        if di0 is None:
            continue
        b0 = next((bi for bi, e in enumerate(bar_end_di) if e >= di0), None)
        if b0 is None:
            continue
        b_end = b0 + WINDOW_BLOCKS
        win_end_di = min(bar_end_di[b_end] if b_end < len(bar_end_di)
                         else len(closes) - 1, len(closes) - 1)
        results = []
        ed = find_above_both(closes, e8d, e21d, di0, win_end_di)
        results.append(("ema_daily", ed))
        # The final block may be partial (today's repaint) — a 16d
        # trigger only counts on a COMPLETED bar, so it is excluded.
        eb = find_above_both(bar_c, e8b, e21b, b0, min(b_end, len(bar_c) - 2))
        results.append(("ema_16d", bar_end_di[eb] if eb is not None else None))
        with conn.cursor() as c:
            for name, edi in results:
                if edi is None:
                    c.execute("""INSERT INTO greendot_entry
                        (dot_id, variant, entered, note)
                        VALUES (%s,%s,false,%s) ON CONFLICT DO NOTHING""",
                        (did, name, _miss(daily_for_miss, di0, win_end_di, px0)))
                    continue
                epx = closes[edi]
                mae, f6, f12 = outcomes(edi, epx)
                c.execute("""INSERT INTO greendot_entry
                    (dot_id, variant, entered, entry_date, entry_px,
                     deployed_frac, mae_pct, fwd6m_pct, fwd12m_pct)
                    VALUES (%s,%s,true,%s,%s,1.0,%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (did, name, dates[edi], round(epx, 4), mae, f6, f12))
    conn.commit()
