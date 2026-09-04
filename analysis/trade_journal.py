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


R_DOLLARS = 250.0     # Eric's manual R (set 2026-09-01) — the yardstick


def r_readings(pnl_dollars, risk_dollars, direction, entry_px, exit_px, stop_px):
    """Pure. (r_multiple, r_actual): r_multiple is R against the $250
    baseline when a dollar P&L exists (the journal's yardstick, decided
    2026-09-01), else the underlying-price R when entry/stop/exit allow
    it; r_actual is R against the trade's own stop-defined dollar risk
    (2026-09-03: "so you are logging my actual +R and the +R against the
    $250?" — both). Anything unknowable is None, never zero."""
    r = None
    if pnl_dollars is not None:
        r = round(pnl_dollars / R_DOLLARS, 2)
    elif entry_px and exit_px and stop_px and entry_px != stop_px:
        sign = 1.0 if direction == "long" else -1.0
        r = round(sign * (exit_px - entry_px) / abs(entry_px - stop_px), 2)
    r_act = None
    if pnl_dollars is not None and risk_dollars:
        r_act = round(pnl_dollars / risk_dollars, 2)
    return r, r_act


def _urls(v):
    """Chart links: a list, or a comma/whitespace-separated string; [] -> None."""
    if not v:
        return None
    if isinstance(v, str):
        v = [u for u in v.replace(",", " ").split() if u]
    out = [str(u).strip() for u in v if str(u).strip()]
    return out or None


def log_trade(ticker, direction, source="eric", setup="", timeframe="",
              instrument="", entered_at="", exited_at="", entry_px=None,
              exit_px=None, stop_px=None, target_px=None, qty=None,
              pnl_dollars=None, note="", mistakes="", risk_dollars=None,
              chart_urls=None) -> str:
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
    pnl = _num(pnl_dollars, "pnl_dollars")
    risk = _num(risk_dollars, "risk_dollars")
    r, r_act = r_readings(pnl, risk, direction, e_px, x_px, s_px)
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO trade_journal
                (source, ticker, direction, instrument, setup, timeframe,
                 entered_at, exited_at, entry_px, exit_px, stop_px,
                 target_px, qty, pnl_dollars, r_multiple, note, mistakes,
                 risk_dollars, r_actual, chart_urls)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id""",
                ((source or "eric").strip().lower(), ticker, direction,
                 instrument.strip() or None, setup.strip() or None,
                 timeframe.strip() or None, ent, ext, e_px, x_px, s_px,
                 _num(target_px, "target_px"), _num(qty, "qty"),
                 pnl, r, note.strip() or None, mistakes.strip() or None,
                 risk, r_act, _urls(chart_urls)))
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
    bits.append(f"R {r:+.2f} on ${R_DOLLARS:g}" if r is not None else "R unavailable"
                if x_px is not None or pnl is not None else "OPEN")
    bits.append(f"real-risk R {r_act:+.2f} (${risk:g} at risk)" if r_act is not None
                else "real-risk R unavailable (no risk_dollars)")
    return " · ".join(bits)


def log_skip(ticker, reason, direction="long", source="eric", setup="",
             timeframe="", at="", spec_id=None, note="", chart_urls=None) -> str:
    """A SKIP is a decision, not a trade (2026-09-04, Eric on the NVDA
    GO he declined: "the skip is data, and the reason for the skip is
    also data"). It writes kind='skip' with NO P&L and NO R — the
    schema refuses an R on a skip — and links the desk spec it declined
    (spec_id) so the book's own outcome on that alert can grade the
    eye. The reason is required: an unexplained skip is a hole, not a
    decision."""
    from screen.reversal_screen import _conn
    ticker = (ticker or "").strip().upper()
    direction = (direction or "long").strip().lower()
    reason = (reason or "").strip()
    if not ticker:
        raise ValueError("ticker is required")
    if direction not in _DIRS:
        raise ValueError("direction must be 'long' or 'short'")
    if not reason:
        raise ValueError("reason is required — a skip without its reason "
                         "is a hole, not a decision")
    when = _parse_ts(at) or dt.datetime.now(dt.timezone.utc)
    sid = int(spec_id) if spec_id not in (None, "", 0) else None
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO trade_journal
                (kind, source, ticker, direction, setup, timeframe,
                 entered_at, skip_reason, spec_id, note, chart_urls)
                VALUES ('skip',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id""",
                ((source or "eric").strip().lower(), ticker, direction,
                 setup.strip() or None, timeframe.strip() or None, when,
                 reason, sid, note.strip() or None, _urls(chart_urls)))
            jid = c.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    bits = [f"Journal #{jid}: SKIPPED {ticker} {direction}"]
    if setup:
        bits.append(setup)
    bits.append(f"reason: {reason}")
    bits.append(f"linked to desk spec #{sid} — the book's outcome grades it"
                if sid else "no desk spec linked (grades only against the tape)")
    return " · ".join(bits)


def _skips_block(c, days) -> list:
    """Render the window's skips — decisions with reasons — each beside
    the desk's own outcome on the alert it declined (a linked spec that
    filled and resolved says what the eye avoided or missed; unfilled /
    unlinked render as such, never as zero)."""
    c.execute("""SELECT j.id, j.entered_at, j.ticker, j.direction, j.setup,
                        j.skip_reason, j.source, j.spec_id, s.book, s.status,
                        t.exit_reason, t.r_multiple, t.exited_at, j.chart_urls
                 FROM trade_journal j
                 LEFT JOIN paper_specs s ON s.id = j.spec_id
                 LEFT JOIN paper_trades t ON t.spec_id = j.spec_id
                 WHERE j.kind = 'skip'
                   AND j.entered_at >= now() - make_interval(days => %s)
                 ORDER BY j.entered_at DESC""", (int(days),))
    rows = c.fetchall()
    if not rows:
        return ["", f"Skips: 0 recorded in the last {days} days (zero is data; "
                    f"log a declined alert with watchtower_journal_skip)."]
    lines = ["", f"Skips — decisions, never R (n={len(rows)}):"]
    resolved = []
    for (jid, at, tk, dr, setup, reason, src, sid, book, status,
         xr, rm, xt, charts) in rows:
        d = at.date().isoformat() if at else "?"
        seg = [f"#{jid} {d} {tk} {dr}"]
        if setup:
            seg.append(setup)
        if sid is None:
            seg.append("no desk spec linked")
        elif book is None:
            seg.append(f"spec #{sid} not found (hole)")
        elif rm is not None:
            seg.append(f"{book} book: {float(rm):+.2f}R ({xr})")
            resolved.append(float(rm))
        elif xt is None and xr is None and status in ("triggered", "filled"):
            seg.append(f"{book} book: OPEN")
        else:
            seg.append(f"{book} book: {status} (no fill)")
        seg.append(f"[{src}]")
        lines.append("  " + " · ".join(seg))
        lines.append(f"      why: {reason}")
        if charts:
            lines.append(f"      📎 {len(charts)} chart(s): " + " ".join(charts))
    if resolved:
        tot = sum(resolved)
        lines.append(f"  Desk's realized R on the alerts skipped: {tot:+.2f}R "
                     f"across {len(resolved)} resolved (n={len(resolved)} — "
                     f"below ~30, anecdote not evidence). Negative = the eye "
                     f"avoided a loss; positive = it missed a winner.")
    return lines


def journal_summary(days=90) -> str:
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, source, ticker, direction, instrument,
                                setup, timeframe, entered_at, exited_at,
                                entry_px, exit_px, stop_px, r_multiple,
                                pnl_dollars, note, mistakes, risk_dollars,
                                r_actual, chart_urls
                         FROM trade_journal
                         WHERE kind = 'trade'
                           AND entered_at >= now() - make_interval(days => %s)
                         ORDER BY r_multiple ASC NULLS LAST,
                                  entered_at DESC""", (int(days),))
            rows = c.fetchall()
            skip_lines = _skips_block(c, days)
    finally:
        conn.close()
    if not rows:
        return (f"Trade journal: 0 trades in the last {days} days. "
                f"Zero is data — log with watchtower_journal_log."
                + "\n".join(skip_lines))
    closed = [r for r in rows if r[12] is not None]
    open_or_hole = [r for r in rows if r[12] is None]
    lines = [f"Trade journal — last {days} days · {len(rows)} entries "
             f"({len(closed)} with R, {len(open_or_hole)} open/R-hole)"]
    if closed:
        tot = sum(float(r[12]) for r in closed)
        wins = sum(1 for r in closed if float(r[12]) > 0)
        lines.append(f"Realized: {tot:+.2f}R on the ${R_DOLLARS:g} baseline · "
                     f"{wins}W/{len(closed) - wins}L "
                     f"(n={len(closed)} — below ~30, anecdote not evidence)")
        with_act = [r for r in closed if r[17] is not None]
        if with_act:
            tot_a = sum(float(r[17]) for r in with_act)
            lines.append(f"Real-risk R: {tot_a:+.2f}R across {len(with_act)} trades "
                         f"with a stop-defined risk; {len(closed) - len(with_act)} "
                         f"without (holes, never zeros)")
    lines.append("")
    lines.append("Worst first (losers lead; a journal that buries them "
                 "is marketing):")
    for r in rows[:25]:
        (jid, src, tk, dr, inst, setup, tf, ent, ext, e_px, x_px, s_px,
         rm, pnl, note, mist, risk, r_act, charts) = r
        d = ent.date().isoformat() if ent else "?"
        rtxt = (f"{float(rm):+.2f}R" if rm is not None
                else ("OPEN" if x_px is None else "R hole"))
        if r_act is not None:
            rtxt += f" (real-risk {float(r_act):+.2f}R on ${float(risk):,.0f})"
        elif rm is not None:
            rtxt += " (real-risk R unavailable)"
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
        if charts:
            lines.append(f"      📎 {len(charts)} chart(s): " + " ".join(charts))
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
    lines.extend(skip_lines)
    return "\n".join(lines)
