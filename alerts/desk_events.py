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
