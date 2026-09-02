"""
Desk event stream → Discord #desk (2026-08-18).

The paper desk narrating itself in real time: fills as they land, exits
with realized R, the 16:20 settle verdicts. This layer READS the record
(paper_trades / paper_specs) and never touches it — notification is
measurement-side, same doctrine as the confirmation shadow. Without it,
the desk's 30+ positions only speak when Eric asks; with it, the whole
system has a voice that reaches a phone.

Delivery is at-most-once per trade event via discord_notify_log claims
(kind 'paper_fill' / 'paper_exit', ref = paper_trades.id), so the 5-min
polling cadence and multiple containers can't double-post. When one
cycle finds several exits, they send worst R first — losers lead, per
the ledger doctrine.
"""
import logging
from zoneinfo import ZoneInfo

log = logging.getLogger("watchtower.desk_events")

ET = ZoneInfo("America/New_York")


def _px(v):
    s = f"{float(v):.2f}"
    return s[:-3] if s.endswith(".00") else s


def format_fill(ticker, direction, setup, entry_px, trigger, stop,
                entered_at_et) -> str:
    prem = ""
    try:
        if trigger is not None and float(entry_px) != float(trigger):
            prem = f" (trigger {_px(trigger)})"
    except (TypeError, ValueError):
        pass
    return (f"🟢 **FILL {entered_at_et} ET** — {ticker} {direction} "
            f"{_px(entry_px)}{prem} · {setup} · stop {_px(stop)}")


def format_exit(ticker, direction, setup, entry_px, exit_px, reason,
                r_multiple, exited_at_et) -> str:
    r_txt = "R n/a" if r_multiple is None else f"{float(r_multiple):+.2f}R"
    icon = "🔴" if (r_multiple is not None and float(r_multiple) < 0) else "🟩"
    return (f"{icon} **EXIT {exited_at_et} ET** — {ticker} {_px(exit_px)} "
            f"· {reason} · {r_txt} (entry {_px(entry_px)}) · {setup}")


KIND_BOOKS = "books_daily"


def format_scoreboard(rows, disagreements, today) -> str:
    """Pure (2026-09-02, Eric: "let's run both and see which wins"):
    per-book running record, worst total R first, plus the day's
    morning-vs-live gamma disagreements. rows = [(book, n, wins,
    losses, total_r)]; disagreements = [(ticker, setup, entered_book,
    cancelled_book)]. Small-n stated on every line."""
    lines = [f"📒 **Books — {today:%a %b %-d}** (running record since "
             f"2026-08-07; n beside every rate — under ~30 it is anecdote)"]
    for book, n, w, l, r in sorted(rows, key=lambda x: (x[4] if x[4] is not None else 0)):
        if not n:
            lines.append(f"{book}: 0 resolved")
            continue
        rate = f"{100.0 * w / n:.0f}%" if n else "n/a"
        lines.append(f"{book}: {n} resolved · {w}-{l} ({rate}) · "
                     f"{r:+.2f}R total")
    g = {b: (n, w, l, r) for b, n, w, l, r in rows if b in ("gamma", "gamma_iday")}
    if "gamma" in g and "gamma_iday" in g:
        (n1, w1, l1, r1), (n2, w2, l2, r2) = g["gamma"], g["gamma_iday"]
        lines.append(f"__Morning board vs live board__: gamma {r1:+.2f}R "
                     f"({w1}-{l1}) · gamma_iday {r2:+.2f}R ({w2}-{l2}) — "
                     f"verdict gate ~20-30 resolved each")
    if disagreements:
        for tk, setup, ent, canc in disagreements:
            lines.append(f"  today they disagreed on {tk} {setup}: "
                         f"{ent} entered, {canc} cancelled (board moved)")
    else:
        lines.append("  today: no morning-vs-live disagreement (zero is data)")
    return "\n".join(lines)


def run_books_scoreboard() -> str:
    """16:59 ET: the books' running record to #desk, once per day.
    Reads the ledger only."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    from alerts.discord_notify import claim_and_send
    from screen.reversal_screen import _conn
    today = _dt.datetime.now(ZoneInfo("America/New_York")).date()
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT s.book, count(*),
                                count(*) FILTER (WHERE t.r_multiple > 0),
                                count(*) FILTER (WHERE t.r_multiple <= 0),
                                round(sum(t.r_multiple)::numeric, 2)
                         FROM paper_trades t JOIN paper_specs s ON s.id=t.spec_id
                         WHERE t.exited_at IS NOT NULL
                         GROUP BY s.book""")
            rows = [(b, int(n), int(w), int(l), float(r) if r is not None else None)
                    for b, n, w, l, r in c.fetchall()]
            # same-day disagreements: one gamma book entered a setup family
            # the other cancelled for 'board moved off level'
            c.execute("""SELECT a.ticker, a.setup, a.book, b.book
                         FROM paper_specs a JOIN paper_specs b
                           ON a.ticker=b.ticker AND a.trade_date=b.trade_date
                          AND split_part(a.setup,'_',1)=split_part(b.setup,'_',1)
                          AND a.book<>b.book
                         WHERE a.trade_date=%s AND a.book IN ('gamma','gamma_iday')
                           AND b.book IN ('gamma','gamma_iday')
                           AND a.status='triggered'
                           AND b.rationale LIKE '%%board moved off level%%'""",
                      (today,))
            dis = [(tk, st, ent, canc) for tk, st, ent, canc in
                   {(r[0], r[1], r[2], r[3]) for r in c.fetchall()}]
        msg = format_scoreboard(rows, dis, today)
        return claim_and_send(KIND_BOOKS, today.isoformat(), "desk", msg,
                              conn=conn)
    finally:
        conn.close()


def run_desk_event_notify() -> dict:
    """Poll today's trade events and post the unsent ones."""
    import time as _time

    from alerts.discord_notify import (POST_SPACING_S, claim_and_send,
                                       is_configured)
    from screen.reversal_screen import _conn

    if not is_configured("desk"):
        return {"off": True}

    conn = _conn()
    fills = exits = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, s.ticker, s.direction, s.setup,
                       s.entry_trigger, s.stop, t.entry_px, t.entered_at,
                       t.exit_px, t.exit_reason, t.r_multiple, t.exited_at
                FROM paper_trades t
                JOIN paper_specs s ON s.id = t.spec_id
                WHERE t.entered_at::date = CURRENT_DATE
                   OR t.exited_at::date = CURRENT_DATE
                ORDER BY t.r_multiple ASC NULLS LAST, t.entered_at
                """
            )
            rows = cur.fetchall()

        import datetime as _dt
        today_et = _dt.datetime.now(ET).date()
        for (tid, ticker, direction, setup, trigger, stop, entry_px,
             entered_at, exit_px, exit_reason, r_mult, exited_at) in rows:
            # Only today's events post: a position entered last week that
            # exits today must not emit a days-late "FILL" on launch day.
            if entered_at is not None \
                    and entered_at.astimezone(ET).date() == today_et:
                msg = format_fill(
                    ticker, direction, setup, entry_px, trigger, stop,
                    entered_at.astimezone(ET).strftime("%H:%M"))
                if fills or exits:
                    _time.sleep(POST_SPACING_S)  # burst pacing (429 lesson)
                if claim_and_send("paper_fill", str(tid), "desk", msg,
                                  conn=conn) == "sent":
                    fills += 1
            if exited_at is not None \
                    and exited_at.astimezone(ET).date() == today_et:
                msg = format_exit(
                    ticker, direction, setup, entry_px, exit_px,
                    exit_reason, r_mult,
                    exited_at.astimezone(ET).strftime("%H:%M"))
                if fills or exits:
                    _time.sleep(POST_SPACING_S)
                if claim_and_send("paper_exit", str(tid), "desk", msg,
                                  conn=conn) == "sent":
                    exits += 1
    finally:
        conn.close()
    if fills or exits:
        log.info(f"[desk-events] posted {fills} fill(s), {exits} exit(s).")
    return {"fills": fills, "exits": exits}
