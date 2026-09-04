"""
The 🎯 morning tickets ping (2026-09-01 — Eric, after the QQQ chop
beating: "How do I make sure I don't miss these trades when they come
up?" / "If I miss one there isn't necessarily one tomorrow").

One #desk message per trading day at 9:20 ET carrying today's ARMED
gamma specs as complete, bracket-ready tickets — direction, entry
trigger, stop, target — plus the swing book's armed count and a
pointer to the 9:51 day-bias verdict. The specs already exist in
paper_specs by then (the 7:30 sweep + morning writer); this ping just
puts them on the phone so a resting bracket can be placed before the
open. A morning with zero armed gamma specs SAYS so — zero is data,
and "no index ticket today" is itself the instruction (hunt the RS
leader instead).

Read-only over the books by signature (tests/test_spec_ping.py):
the module can never write paper_specs/paper_trades. At-most-once
per day via discord_notify_log claims, same as every other stream.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.spec_ping")

CHANNEL = "desk"
KIND = "morning_tickets"
FIRST_TICK = dt.time(9, 18)


def _px(v):
    s = f"{float(v):.2f}"
    return s[:-3] if s.endswith(".00") else s


SKIP_LABELS = {"skipped_binary": "binary-day skip (the desk stands aside on the print)",
               "skipped_regime": "regime skip", "skipped_stale": "stale-board skip"}


def format_tickets(gamma_rows, swing_n: int, skips=None) -> str:
    """Pure: the morning tickets message. gamma_rows = [(ticker,
    direction, setup, trigger, stop, target), ...] for today's ARMED
    gamma specs; skips = [(ticker, setup, status), ...] for specs the
    books declined. Zero armed renders as the stated quiet read, and a
    SKIP renders as the decision it is (2026-09-04, Eric on NFP day:
    "why do we not have any gamma tickets?" — the message said 0 armed
    and not why)."""
    lines = ["🎯 **MORNING TICKETS** — armed before the open"]
    if gamma_rows:
        for tk, direction, setup, trig, stop, tgt in gamma_rows:
            side = "SHORT" if direction == "short" else "LONG"
            lines.append(
                f"**{tk} {side}** ({setup}) — entry {_px(trig)} · "
                f"stop {_px(stop)} · target {_px(tgt)} — bracket-ready")
        lines.append("Fades rest AT the level: place the bracket, walk "
                     "away. A no-fill day costs nothing.")
    else:
        lines.append("No gamma tickets today (0 armed — that is the "
                     "reading). Hunt the RS leader on the scanner instead.")
    if skips:
        by = {}
        for tk, setup, status in skips:
            by.setdefault(status, []).append(f"{tk} {setup}")
        for status, items in by.items():
            lines.append(f"Skipped — {SKIP_LABELS.get(status, status)}: "
                         + ", ".join(items)
                         + (". The 10:30 shadow re-arm grades the skip's cost."
                            if status == "skipped_binary" else "."))
    lines.append(f"Swing book: {swing_n} armed (details in the brief). "
                 f"📐 Day-bias verdict pings at 9:31 (9:51 fallback).")
    return "\n".join(lines)


def run_spec_ping() -> dict:
    """Read today's armed specs and deliver the tickets once."""
    from alerts.discord_notify import claim_and_send, is_configured
    from analysis.paper_trader import ET
    from screen.reversal_screen import _conn

    now = dt.datetime.now(ET)
    if now.weekday() >= 5 or now.time() < FIRST_TICK:
        return {"skip": "outside window"}
    if not is_configured(CHANNEL):
        return {"off": True}
    today = now.date()
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT ticker, direction, setup, entry_trigger,
                                stop, target
                         FROM paper_specs
                         WHERE trade_date=%s AND book IN ('gamma','gamma_iday')
                           AND status='armed'
                         ORDER BY ticker""", (today,))
            gamma_rows = c.fetchall()
            c.execute("""SELECT count(*) FROM paper_specs
                         WHERE trade_date=%s AND book='swing'
                           AND status='armed'""", (today,))
            swing_n = c.fetchone()[0]
            c.execute("""SELECT DISTINCT ticker, setup, status FROM paper_specs
                         WHERE trade_date=%s AND book IN ('gamma','gamma_iday')
                           AND status LIKE 'skipped%%'
                         ORDER BY ticker""", (today,))
            skips = c.fetchall()
        msg = format_tickets(
            [(r[0], r[1], r[2], float(r[3]), float(r[4]),
              float(r[5]) if r[5] is not None else float(r[3])) for r in gamma_rows],
            swing_n, skips)
        out = claim_and_send(KIND, today.isoformat(), CHANNEL, msg, conn=conn)
        return {"sent": out, "gamma": len(gamma_rows), "swing": swing_n}
    finally:
        conn.close()
