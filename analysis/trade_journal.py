"""
Eric's trade journal (2026-09-02 — "I want a journal/ledger that I can
put in here on all trades I take so I and we can analyze them later.
I will also use grok bot to enter those journal entries").

This is ERIC'S book — his manual day/swing trades — never the paper
desk's. It records what he actually did, in his words, so the record
can later be graded the way every desk experiment is: setups named in
the Scanner/Bot vocabulary, losers lead, counts beside any rate below
the small-n bar (~30). The Grok read-only rule covers code and the
DESK ledger; this table is exactly the place outside sessions (Eric,
the Grok bot) are MEANT to write, and it is the only table this
module touches.

R is computed in UNDERLYING terms when entry/stop/exit allow it and
never fabricated: an options trade without underlying exit renders
its dollar P&L and an R hole, not a guess. Missing fields are holes
and render as holes.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.journal")

_DIRS = ("long", "short")


def _parse_ts(s):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
        if t.tzinfo is None:
            from zoneinfo import ZoneInfo
            t = t.replace(tzinfo=ZoneInfo("America/New_York"))
        return t
    except ValueError:
        raise ValueError(f"unparseable timestamp {s!r} — use ISO format, "
                         f"e.g. 2026-09-02T10:35 (ET assumed if no zone)")


def _num(v, name):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {v!r}")


def log_trade(ticker, direction, source="eric", setup="", timeframe="",
              instrument="", entered_at="", exited_at="", entry_px=None,
              exit_px=None, stop_px=None, target_px=None, qty=None,
              pnl_dollars=None, note="", mistakes="") -> str:
    from screen.reversal_screen import _conn
    ticker = (ticker or "").strip().upper()
    direction = (direction or "").strip().lower()
    if not ticker:
        raise ValueError("ticker is required")
    if direction not in _DIRS:
        raise ValueError("direction must be 'long' or 'short'")
    ent = _parse_ts(entered_at) or dt.datetime.now(dt.timezone.utc)
    ext = _parse_ts(exited_at)
    e_px = _num(entry_px, "entry_px")
    x_px = _num(exit_px, "exit_px")
    s_px = _num(stop_px, "stop_px")
    r = None
    if e_px and x_px and s_px and e_px != s_px:
        sign = 1.0 if direction == "long" else -1.0
        r = round(sign * (x_px - e_px) / abs(e_px - s_px), 2)
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO trade_journal
                (source, ticker, direction, instrument, setup, timeframe,
                 entered_at, exited_at, entry_px, exit_px, stop_px,
                 target_px, qty, pnl_dollars, r_multiple, note, mistakes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id""",
                ((source or "eric").strip().lower(), ticker, direction,
                 instrument.strip() or None, setup.strip() or None,
                 timeframe.strip() or None, ent, ext, e_px, x_px, s_px,
                 _num(target_px, "target_px"), _num(qty, "qty"),
                 _num(pnl_dollars, "pnl_dollars"), r,
                 note.strip() or None, mistakes.strip() or None))
            jid = c.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    bits = [f"Journal #{jid}: {ticker} {direction}"]
    if setup:
        bits.append(setup)
    if e_px is not None:
        bits.append(f"in {e_px:g}")
    if s_px is not None:
        bits.append(f"stop {s_px:g}")
    if x_px is not None:
        bits.append(f"out {x_px:g}")
    bits.append(f"R {r:+.2f}" if r is not None else "R unavailable"
                if x_px is not None else "OPEN")
    return " · ".join(bits)


def journal_summary(days=90) -> str:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, source, ticker, direction, instrument,
                                setup, timeframe, entered_at, exited_at,
                                entry_px, exit_px, stop_px, r_multiple,
                                pnl_dollars, note, mistakes
                         FROM trade_journal
                         WHERE entered_at >= now() - make_interval(days => %s)
                         ORDER BY r_multiple ASC NULLS LAST,
                                  entered_at DESC""", (int(days),))
            rows = c.fetchall()
    finally:
        conn.close()
    if not rows:
        return (f"Trade journal: 0 entries in the last {days} days. "
                f"Zero is data — log with watchtower_journal_log.")
    closed = [r for r in rows if r[12] is not None]
    open_or_hole = [r for r in rows if r[12] is None]
    lines = [f"Trade journal — last {days} days · {len(rows)} entries "
             f"({len(closed)} with R, {len(open_or_hole)} open/R-hole)"]
    if closed:
        tot = sum(float(r[12]) for r in closed)
        wins = sum(1 for r in closed if float(r[12]) > 0)
        lines.append(f"Realized: {tot:+.2f}R · {wins}W/{len(closed) - wins}L "
                     f"(n={len(closed)} — below ~30, anecdote not evidence)")
    lines.append("")
    lines.append("Worst first (losers lead; a journal that buries them "
                 "is marketing):")
    for r in rows[:25]:
        (jid, src, tk, dr, inst, setup, tf, ent, ext, e_px, x_px, s_px,
         rm, pnl, note, mist) = r
        d = ent.date().isoformat() if ent else "?"
        rtxt = (f"{float(rm):+.2f}R" if rm is not None
                else ("OPEN" if x_px is None else "R hole"))
        seg = [f"#{jid} {d} {tk} {dr}", rtxt]
        if setup:
            seg.append(setup)
        if tf:
            seg.append(tf)
        if e_px is not None:
            px = f"in {float(e_px):g}"
            if s_px is not None:
                px += f" stop {float(s_px):g}"
            if x_px is not None:
                px += f" out {float(x_px):g}"
            seg.append(px)
        if pnl is not None:
            seg.append(f"${float(pnl):+,.0f}")
        seg.append(f"[{src}]")
        lines.append("  " + " · ".join(seg))
        if mist:
            lines.append(f"      ⚠ {mist}")
        elif note:
            lines.append(f"      {note}")
    if len(rows) > 25:
        lines.append(f"  … {len(rows) - 25} more not shown "
                     f"(count stated, never silently dropped)")
    setups = {}
    for r in rows:
        if r[12] is not None:
            setups.setdefault(r[5] or "(unlabeled)", []).append(float(r[12]))
    if setups:
        lines.append("")
        lines.append("By setup (closed trades only):")
        for s, rs in sorted(setups.items(), key=lambda kv: sum(kv[1])):
            w = sum(1 for x in rs if x > 0)
            lines.append(f"  {s}: {sum(rs):+.2f}R · {w}W/{len(rs) - w}L "
                         f"(n={len(rs)})")
    return "\n".join(lines)
