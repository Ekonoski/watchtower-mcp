"""
The 📐 day-bias verdict ping (2026-08-25, Eric: "ok build the ping
tonight after close" — after asking "will that send direct to my
discord?").

One message per trading day to #desk, at the first tick after the
book's own 9:50 decision pass, stating the day's verdict:

  ARMED       — 9:30 opened above PDH; the late-retest entry is live
                from 10:30 (an earlier touch cancels the day).
  STAND-ASIDE — opened at/below PDH; the coin-flip bucket. Zero is
                data: the stand-aside is stated, never silent.
  CANCELLED   — the early touch fired before the verdict could send
                (late boot), stated as what it is.
  unavailable — no spec exists by 10:00 ET; a hole is a hole, not a
                quiet day (the _social_block family).

Plus one follow-up ping the day the verdict CHANGES: an early touch
cancelling an ARMED day (2026-08-25 proved the gap — the first
cancelled_early happened silently and Eric had to ask). Delivery is
at-most-once per (kind, date) via discord_notify_log claims, same as
every other stream. The ping READS the book's record — spec status and
recorded bars — and never decides anything itself; the module cannot
write paper_specs or paper_trades, pinned by signature in
tests/test_day_bias_ping.py.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.day_bias_ping")

CHANNEL = "desk"
KIND_VERDICT = "day_bias_verdict"
KIND_CANCEL = "day_bias_cancel"
FIRST_TICK = dt.time(9, 51)     # book decides at 9:50; ping rides behind
HOLE_AFTER = dt.time(10, 0)     # no spec by then = a hole worth stating


def _px(v):
    s = f"{float(v):.2f}"
    return s[:-3] if s.endswith(".00") else s


def format_verdict(status: str, pdh, stop, open_px=None) -> str:
    """Pure: the verdict message for a spec status. open_px may be None
    (renders as unavailable, never invented)."""
    o = _px(open_px) if open_px is not None else "unavailable"
    if status == "skipped_bias":
        return (f"📐 **SPY** DAY BIAS — **STAND-ASIDE**\n"
                f"9:30 open {o} ≤ PDH {_px(pdh)} — inside/flat open is "
                f"the coin-flip bucket (52/48). No trade today; the "
                f"stand-aside is the decision.")
    if status == "cancelled":
        return format_cancel(pdh, None)
    filled = " (already filled)" if status == "triggered" else ""
    return (f"📐 **SPY** DAY BIAS — **ARMED** (long-bias day){filled}\n"
            f"9:30 open {o} > PDH {_px(pdh)} → 80% close green (n=5,443)\n"
            f"Entry: retest of {_px(pdh)} at/after 10:30 ONLY — an earlier "
            f"touch cancels the day (graded chop)\n"
            f"Stop {_px(stop)} on 15m closes · exit true close · "
            f"study 69% win, +27bps, MFE~2xMAE (n=273)")


def format_cancel(pdh, touch_et: str | None) -> str:
    """Pure: the verdict-change ping when an early touch kills an armed
    day. touch_et is the recorded bar's ET label, or None (unavailable)."""
    at = f" (bar {touch_et})" if touch_et else ""
    return (f"📐 **SPY** DAY BIAS — **CANCELLED** (early touch)\n"
            f"PDH {_px(pdh)} touched before 10:30{at} — the graded chop "
            f"bucket. No trade today; the cancel IS the discipline.")


def format_hole() -> str:
    return ("📐 SPY DAY BIAS — *unavailable*: no spec recorded by 10:00 ET "
            "(see ingestion_log, job day_bias_loop). A hole, not a verdict.")


def run_daybias_ping() -> dict:
    """Read the day_bias book's record and deliver the day's verdict —
    and, if an armed day was cancelled by an early touch, the change."""
    from alerts.discord_notify import claim_and_send, is_configured
    from analysis.paper_trader import ET
    from screen.reversal_screen import _conn

    now = dt.datetime.now(ET)
    if now.weekday() >= 5 or now.time() < FIRST_TICK:
        return {"skip": "outside window"}
    if not is_configured(CHANNEL):
        return {"off": True}
    today = now.date()
    ref = today.isoformat()
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT status, entry_trigger, stop, rationale
                         FROM paper_specs
                         WHERE book='day_bias' AND trade_date=%s""", (today,))
            spec = c.fetchone()
        if spec is None:
            if now.time() >= HOLE_AFTER:
                out = claim_and_send(KIND_VERDICT, ref, CHANNEL,
                                     format_hole(), conn=conn)
                return {"verdict": out, "state": "hole"}
            return {"skip": "no spec yet"}
        status, pdh, stop = spec[0], float(spec[1]), float(spec[2])
        early_cancel = (status == "cancelled"
                        and "touched before 10:30" in (spec[3] or ""))

        with conn.cursor() as c:
            c.execute("""SELECT open FROM paper_spec_bars
                         WHERE ticker='SPY' AND trade_date=%s
                           AND (ts AT TIME ZONE 'America/New_York')::time
                               = '09:30'""", (today,))
            r = c.fetchone()
        open_px = float(r[0]) if r else None

        res = {}
        res["verdict"] = claim_and_send(KIND_VERDICT, ref, CHANNEL,
                                        format_verdict(status, pdh, stop,
                                                       open_px), conn=conn)
        if early_cancel:
            touch_et = None
            with conn.cursor() as c:
                c.execute("""SELECT (ts AT TIME ZONE 'America/New_York')::time
                             FROM paper_spec_bars
                             WHERE ticker='SPY' AND trade_date=%s
                               AND low <= %s
                               AND (ts AT TIME ZONE 'America/New_York')::time
                                   BETWEEN '09:30' AND '10:29'
                             ORDER BY ts LIMIT 1""", (today, pdh))
                r = c.fetchone()
            if r:
                touch_et = str(r[0])[:5]
            if res["verdict"] == "duplicate":
                # Verdict went out earlier as ARMED — this is the change.
                res["cancel"] = claim_and_send(KIND_CANCEL, ref, CHANNEL,
                                               format_cancel(pdh, touch_et),
                                               conn=conn)
            else:
                # This tick's verdict already said CANCELLED; claim the
                # cancel ref so a later tick can't double-announce.
                with conn.cursor() as c:
                    c.execute("""INSERT INTO discord_notify_log
                                 (kind, ref, channel, delivered)
                                 VALUES (%s, %s, %s, true)
                                 ON CONFLICT (kind, ref) DO NOTHING""",
                              (KIND_CANCEL, ref, CHANNEL))
                conn.commit()
        return res
    finally:
        conn.close()
