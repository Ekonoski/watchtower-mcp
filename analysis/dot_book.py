"""
The dot book — Eric's live swing ledger (2026-08-29, set up the
weekend the framework was graded; the book existed first, entered by
eye, and four of five 16D entries landed on real recorded dots).

Doctrine: the ledger grades the signal — every position carries its
dot anchor (date, price, cross depth, drawdown at the dot) so live
results compare to study priors the way every paper book compares to
its backtest. An entry price the ledger doesn't have renders as a
HOLE ('entry px: unlogged'), never a guess. Exits record their
reason. One open row per ticker. Sizing stays Eric's — size_note is
free text, never parsed.

Render shows, per open position: entry vs dot basis, live price
(latest daily close, stamped), unrealized from entry AND from dot,
days held, planned adds, the stop line, and the cohort prior line so
expectation sits beside reality. Closed positions render worst
realized first (losers lead).
"""
import logging

log = logging.getLogger("watchtower.dot_book")

PRIOR_LINE = ("deep-dot cohort prior: med 6-mo +7.7%, 35% run 50%+ "
              "within a year, expect ~-15/-20% excursion; "
              "survivors-only backtest")


def log_entry(ticker, entry_kind, thesis="", entry_date=None, entry_px=None,
              size_note="", stop_line="", add1_px=None, add2_px=None):
    from screen.reversal_screen import _conn
    tk = ticker.upper().strip()
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT dot_date, px_at_dot, cross_depth,
                                drawdown_pct
                         FROM greendot_dots WHERE ticker=%s
                         ORDER BY dot_date DESC LIMIT 1""", (tk,))
            dot = c.fetchone()
            c.execute("""INSERT INTO dot_book
                (ticker, entry_kind, entry_date, entry_px, size_note,
                 dot_date, dot_px, cross_depth, drawdown_pct,
                 add1_px, add2_px, stop_line, thesis)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id""",
                (tk, entry_kind, entry_date, entry_px, size_note,
                 dot[0] if dot else None, dot[1] if dot else None,
                 dot[2] if dot else None, dot[3] if dot else None,
                 add1_px, add2_px, stop_line, thesis))
            rid = c.fetchone()[0]
        conn.commit()
        anchor = (f"anchored to dot {dot[0]} @ {dot[1]} (cross {dot[2]}, "
                  f"dd {dot[3]}%)" if dot else
                  "no recorded dot — unanchored position")
        return f"#{rid} {tk} logged ({entry_kind}) — {anchor}"
    finally:
        conn.close()


def log_exit(ticker, exit_px, exit_reason, exit_date=None):
    from screen.reversal_screen import _conn
    tk = ticker.upper().strip()
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""UPDATE dot_book
                         SET status='closed', exit_px=%s, exit_reason=%s,
                             exit_date=COALESCE(%s, CURRENT_DATE)
                         WHERE ticker=%s AND status='open'
                         RETURNING id, entry_px""",
                      (exit_px, exit_reason, exit_date, tk))
            row = c.fetchone()
        conn.commit()
        if row is None:
            return f"No open {tk} position in the dot book."
        rid, epx = row
        if epx is None:
            return f"#{rid} {tk} closed at {exit_px} ({exit_reason}) — " \
                   f"realized return UNKNOWN: entry px was never logged."
        ret = (float(exit_px) / float(epx) - 1) * 100
        return f"#{rid} {tk} closed at {exit_px} ({exit_reason}) — " \
               f"{ret:+.1f}% realized."
    finally:
        conn.close()


def render_book():
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT b.ticker, b.entry_kind, b.entry_date,
                                b.entry_px, b.dot_date, b.dot_px,
                                b.cross_depth, b.drawdown_pct, b.add1_px,
                                b.add2_px, b.stop_line, b.thesis,
                                p.trade_date, p.close
                         FROM dot_book b
                         LEFT JOIN LATERAL (
                           SELECT trade_date, close FROM daily_prices d
                           WHERE d.ticker=b.ticker AND d.close IS NOT NULL
                           ORDER BY trade_date DESC LIMIT 1) p ON true
                         WHERE b.status='open' ORDER BY b.ticker""")
            open_rows = c.fetchall()
            c.execute("""SELECT ticker, entry_px, exit_px, exit_reason,
                                exit_date
                         FROM dot_book WHERE status='closed'
                         ORDER BY CASE WHEN entry_px IS NULL OR exit_px IS NULL
                                  THEN 0 ELSE exit_px/entry_px END ASC
                         LIMIT 20""")
            closed = c.fetchall()
    finally:
        conn.close()
    if not open_rows and not closed:
        return ("DOT BOOK — empty. Log entries with "
                "watchtower_dot_entry; the graded pipeline is the "
                "greendot screen.")
    lines = [f"DOT BOOK — {len(open_rows)} open · {PRIOR_LINE}", ""]
    for (tk, kind, edate, epx, ddate, dpx, depth, dd, a1, a2, stop,
         thesis, pdate, close) in open_rows:
        px_line = (f"live {close} ({pdate})" if close is not None
                   else "live px UNAVAILABLE")
        if epx is not None and close is not None:
            upl = (float(close) / float(epx) - 1) * 100
            entry_part = f"entry {epx} ({edate}) · {upl:+.1f}%"
        elif epx is not None:
            entry_part = f"entry {epx} ({edate})"
        else:
            entry_part = "entry px: UNLOGGED (hole — supply the fill)"
        if dpx is not None and close is not None:
            from_dot = (float(close) / float(dpx) - 1) * 100
            dot_part = (f"dot {ddate} @ {dpx} (cross {depth}, dd {dd}%) "
                        f"· {from_dot:+.1f}% from dot")
        else:
            dot_part = "no dot anchor"
        adds = f"adds {a1}/{a2}" if a1 or a2 else "no adds planned"
        lines.append(f"  {tk} [{kind}] · {entry_part} · {px_line}")
        lines.append(f"      {dot_part} · {adds} · "
                     f"stop: {stop or 'UNDEFINED (set one)'}")
        if thesis:
            lines.append(f"      thesis: {thesis}")
    if closed:
        lines += ["", "  CLOSED (worst first — losers lead):"]
        for tk, epx, xpx, reason, xdate in closed:
            if epx is not None and xpx is not None:
                r = (float(xpx) / float(epx) - 1) * 100
                lines.append(f"    {tk} {r:+.1f}% ({reason}, {xdate})")
            else:
                lines.append(f"    {tk} return UNKNOWN — entry or exit "
                             f"px unlogged ({reason}, {xdate})")
    return "\n".join(lines)
