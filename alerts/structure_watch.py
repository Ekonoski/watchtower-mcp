"""
The intraday structure watcher (2026-08-24, Eric — the Discord
trader's workflow, systematized: "He trades this on an intraday time
frame using 1 hour/15 min/5 min").

The division of labor: the NIGHTLY structure screen finds the major
levels (multi-touch shelves, broken on a daily close, retest lifecycle
classified); THIS job follows the freshest bullish breakout/retest
names through the session on 15-minute bars and pings the moment one
prints a DEFENDED retest at its level — Eric's volume signature
(find_defense v1, the variant that graded best: 57.6% / +0.94R at
22,360 episodes) applied at major structure.

Rules of the road:
- Watch prompt, never an entry: this is a screen extension, graded by
  forward returns per the harness doctrine. It arms nothing.
- One ping per (day, ticker, level) via the shared notify-log claims.
- Touches and verdicts persist to structure_watch so the forward-
  return grading reads from a record, not from memory.
- The knife guard rides along: a 15m CLOSE 3% under the level before
  defense marks the row knife_skipped — recorded, never pinged.
- Errors land in ingestion_log (the 2026-08-24 lesson, twice learned:
  a job that only stdout hears is a hole in the record).
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.structure_watch")

MAX_WATCH = 120          # freshest bullish breakout/retest names per day
NEAR_BAND = (-2.0, 6.0)  # last_close within this % of the level to watch
KNIFE_PCT = 0.03         # close 3% under the level = knife, recorded
BUDGET_S = 10 * 60


def watch_ref(trade_date, ticker: str, level) -> str:
    return f"{trade_date}:{ticker}:{float(level):.2f}"


def format_watch_alert(ticker: str, level: float, touches: int, res: dict,
                       at_et: str) -> str:
    prem = res.get("premium_pct")
    return (f"🏗 **{ticker}** DEFENDED retest at major structure "
            f"{level:g} ({touches}-touch shelf) — defense close "
            f"{res['px']:g} ({prem*100:+.2f}% over level) at {at_et} ET, "
            f"vol {res['defense_vol']:.0f} > pullback base "
            f"{res['base_vol']:.0f}\n"
            f"(watch prompt, not an entry · the defense signature at a "
            f"broken shelf · screen-graded by forward returns · one ping "
            f"per level per day)")


def first_touch_idx(bars, level: float):
    """Pure: index of the first RTH bar whose low reached the level."""
    return next((i for i, b in enumerate(bars) if b[4] <= level), None)


def run_structure_watch() -> dict:
    from zoneinfo import ZoneInfo

    from alerts.discord_notify import (POST_SPACING_S, claim_and_send,
                                       is_configured)
    from analysis.defense_shadow import find_defense
    from analysis.paper_trader import ET, _last_closed_15m, _rth
    from screen.reversal_screen import _conn

    now = dt.datetime.now(ET)
    if now.weekday() >= 5 or not (dt.time(9, 50) <= now.time() <= dt.time(15, 55)):
        return {"off_hours": True}
    today = now.date()
    t0 = time.time()
    conn = _conn()
    touched = defended = pinged = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, level, touches FROM structure_screen
                WHERE run_date = (SELECT max(run_date) FROM structure_screen)
                  AND direction='bullish' AND state IN ('breakout','retest')
                  AND dist_pct BETWEEN %s AND %s
                ORDER BY break_date DESC, touches DESC
                LIMIT %s
                """, (NEAR_BAND[0], NEAR_BAND[1], MAX_WATCH))
            watchlist = cur.fetchall()
        for tk, level, touches in watchlist:
            if time.time() - t0 > BUDGET_S:
                log.warning(f"[structure-watch] budget hit — partial pass "
                            f"({touched} touched so far).")
                break
            level = float(level)
            raw = _rth(_last_closed_15m(tk))
            if not raw:
                continue
            ti = first_touch_idx(raw, level)
            if ti is None:
                continue
            touched += 1
            # tuple bars (ts, open, close, high, low, vol) -> the dict
            # contract find_defense is pinned against.
            bars = [{"ts": b[0], "open": b[1], "close": b[2], "high": b[3],
                     "low": b[4], "volume": b[5] if len(b) > 5 else None}
                    for b in raw]
            res = find_defense(bars, level, level * (1 - KNIFE_PCT), ti)["v1"]
            status = res["status"]
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO structure_watch
                        (trade_date, ticker, level, touches, touch_at,
                         status, defense_px, defense_at, premium_pct)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (trade_date, ticker, level) DO UPDATE
                       SET status = EXCLUDED.status,
                           defense_px = EXCLUDED.defense_px,
                           defense_at = EXCLUDED.defense_at,
                           premium_pct = EXCLUDED.premium_pct,
                           updated_at = now()
                    """,
                    (today, tk, level, touches, raw[ti][0], status,
                     res.get("px"), res.get("at"), res.get("premium_pct")))
            conn.commit()
            if status == "defended":
                defended += 1
                if is_configured("gamma"):
                    at_et = res["at"].astimezone(
                        ZoneInfo("America/New_York")).strftime("%H:%M")
                    msg = format_watch_alert(tk, level, int(touches), res,
                                             at_et)
                    if claim_and_send("structure_watch",
                                      watch_ref(today, tk, level),
                                      "gamma", msg, conn) == "sent":
                        pinged += 1
                        time.sleep(POST_SPACING_S)
    finally:
        conn.close()
    if touched:
        log.info(f"[structure-watch] {touched} touched, {defended} defended, "
                 f"{pinged} pinged.")
    return {"touched": touched, "defended": defended, "pinged": pinged}
