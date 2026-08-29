"""
The gated-ladder study (2026-08-29, Eric: "run the tranche-pause study
tonight" — the follow-up his daily 8/21 knife-detector earned).

Pre-registered rule, frozen before any number: the bounded ladder
(1/3 at dot / -15% / -25%, fixed total) with ONE change — tranches 2
and 3 only ARM once the name has printed a daily 8/21 reclaim (a
daily close above BOTH EMAs) after the dot, and then fill on the next
touch of their levels. The reclaim must complete STRICTLY BEFORE the
touch day (proof precedes action — the wick-rule spirit). A name that
never reclaims gets tranche 1 only: the -9%-forward no-clear cohort
is walled off from the add money, without paying any entry premium —
the tranches were already planned.

Graded per deep-16D-dot beside the baseline: variant 'ladder_gated'
in greendot_entry, same conventions as 'ladder' (avg px of DEPLOYED
capital, deployed_frac, MAE / fwd 6m / fwd 12m from the dot date).
Writes only greendot_entry; resume by ticker; marker retires it.
"""
import logging

log = logging.getLogger("watchtower.greendot_gate")

COMPLETE_MARKER = "greendot_gate_v1"


def gated_fills(px0, lows, closes, e8, e21, di0, win_end, ladder):
    """Pure. Returns (fills, first_clear_di). Tranche 1 always fills
    at px0. Tranches at ladder[1:] fill on the first touch of their
    level that happens AFTER the first daily close above both EMAs
    following the dot; no reclaim → no adds."""
    fills = [px0]
    first_clear = None
    for j in range(di0 + 1, win_end + 1):
        if closes[j] > e8[j] and closes[j] > e21[j]:
            first_clear = j
            break
    if first_clear is None:
        return fills, None
    for lvl in ladder[1:]:
        tpx = px0 * (1 + lvl)
        hit = next((i for i in range(first_clear + 1, win_end + 1)
                    if lows[i] <= tpx), None)
        if hit is not None:
            fills.append(tpx)
    return fills, first_clear


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
                                             AND e.variant = 'ladder_gated')
                         ORDER BY g.ticker LIMIT %s""", (batch,))
            todo = [r[0] for r in c.fetchall()]
        if not todo:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                          "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                          (COMPLETE_MARKER,))
            conn.commit()
            log.info("[greendot-gate] complete.")
            return True
        for tk in todo:
            try:
                _one_ticker(conn, tk, cal)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot-gate] %s failed: %s", tk, str(e)[:300])
                with conn.cursor() as c:
                    c.execute("""INSERT INTO greendot_entry
                                 (dot_id, variant, entered, note)
                                 SELECT id, 'ladder_gated', false,
                                        'ticker_error'
                                 FROM greendot_dots WHERE ticker=%s
                                 ON CONFLICT DO NOTHING""", (tk,))
                conn.commit()
        log.info("[greendot-gate] processed %d ticker(s).", len(todo))
        return False
    finally:
        conn.close()


def _one_ticker(conn, tk, cal):
    from analysis.greendot_ema_entry import ema
    from analysis.greendot_entry_study import LADDER, WINDOW_BLOCKS
    from analysis.greendot_study import blocks_16d
    with conn.cursor() as c:
        c.execute("""SELECT id, dot_date, px_at_dot FROM greendot_dots
                     WHERE ticker=%s ORDER BY dot_date""", (tk,))
        dots = c.fetchall()
        c.execute("""SELECT trade_date, close, COALESCE(low, close)
                     FROM daily_prices
                     WHERE ticker=%s AND close IS NOT NULL
                     ORDER BY trade_date""", (tk,))
        rows = c.fetchall()
    if not dots or len(rows) < 100:
        return
    dates = [r[0] for r in rows]
    closes = [float(r[1]) for r in rows]
    lows = [float(r[2]) for r in rows]
    e8, e21 = ema(closes, 8), ema(closes, 21)
    blk = blocks_16d(dates, cal)
    bar_end_di, cur_id = [], None
    for i in range(len(dates)):
        if blk[i] != cur_id:
            cur_id = blk[i]
            bar_end_di.append(i)
        else:
            bar_end_di[-1] = i
    date_to_di = {d: i for i, d in enumerate(dates)}
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
        fills, first_clear = gated_fills(px0, lows, closes, e8, e21,
                                         di0, win_end_di, LADDER)
        avg = sum(fills) / len(fills)
        seg = closes[di0 + 1: di0 + 127]
        mae = round((min(seg) / avg - 1) * 100, 2) if seg else None
        f6 = round((closes[di0 + 126] / avg - 1) * 100, 2) \
            if di0 + 126 < len(closes) else None
        f12 = round((closes[di0 + 252] / avg - 1) * 100, 2) \
            if di0 + 252 < len(closes) else None
        with conn.cursor() as c:
            c.execute("""INSERT INTO greendot_entry
                (dot_id, variant, entered, entry_date, entry_px,
                 deployed_frac, mae_pct, fwd6m_pct, fwd12m_pct, note)
                VALUES (%s,'ladder_gated',true,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""",
                (did, d0, round(avg, 4), round(len(fills) / 3, 2),
                 mae, f6, f12,
                 None if first_clear is not None else 'never_reclaimed'))
    conn.commit()
