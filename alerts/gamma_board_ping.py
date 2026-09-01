"""
The morning gamma board on the phone (2026-09-01, Eric: "Can we set it
up so that the morning gamma board gets sent directly to the discord
during the pre-market and then continues to send its changes throught
the trading session?"). The intraday changes were already live
(gamma_drift / gamma_prox); this module is the missing premarket half:
one 🌅 message at ~8:05 ET with the 7:30 sweep's board — the same
marks the 9:20 drift baseline will hold — so the day starts from a
known board instead of the first alert arriving contextless.

Read-only over every table; at-most-once per day via the
discord_notify_log claim; a ticker without a fresh board renders as a
NAMED hole, never silently dropped (the _social_block rule); each row
stamps its own computed_at time (stamp freshness per row, not per
page). Inverted walls — put wall ABOVE spot or call wall BELOW spot —
carry the ⚠ doctrine read inline, same as the drift alerts.
"""
import datetime as dt
import logging

from alerts.discord_notify import claim_and_send
from analysis.gex import DRIFT_TICKERS

log = logging.getLogger("watchtower.gamma_board")

CHANNEL = "gamma"
KIND_BOARD = "gamma_board"
ET = "America/New_York"
VENUES = ("SPY", "QQQ", "IWM")


def _fmt_row(tk, row):
    """One board line. row = (spot, call_wall, put_wall, gamma_flip,
    net_gex, regime, computed_at_et) with any element possibly None."""
    spot, cw, pw, gf, ng, regime, ts = row
    if spot is None:
        return f"{tk}: *unavailable* (no board row)"
    bits = [f"**{tk}** {spot:.2f}"]
    bits.append(f"CW {cw:.0f}" if cw is not None else "CW *n/a*")
    bits.append(f"PW {pw:.0f}" if pw is not None else "PW *n/a*")
    bits.append(f"flip {gf:.2f}" if gf is not None else "flip *n/a*")
    if ng is not None:
        bits.append(f"netGEX {ng:+.1f}")
    if regime:
        bits.append(regime)
    line = " · ".join(bits)
    warns = []
    if pw is not None and spot is not None and pw > spot:
        warns.append("⚠ put wall ABOVE spot — stranded protection "
                     "overhead, not a floor")
    if cw is not None and spot is not None and cw < spot:
        warns.append("⚠ call wall BELOW spot — positive-gamma "
                     "stabilizer underneath, not a cap")
    if warns:
        line += "\n  " + " · ".join(warns)
    if ts is not None:
        line += f"  _({ts:%H:%M})_"
    return line


def run_board_ping() -> str:
    """~8:05 ET: the morning board, once per day."""
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    today = dt.datetime.now(ZoneInfo(ET)).date()
    conn = _conn()
    try:
        rows = {}
        for tk in VENUES + tuple(t for t in DRIFT_TICKERS
                                 if t not in VENUES):
            with conn.cursor() as c:
                c.execute(
                    """SELECT spot, call_wall, put_wall, gamma_flip,
                              net_gex, regime,
                              computed_at AT TIME ZONE 'America/New_York'
                       FROM gex_levels
                       WHERE ticker = %s AND computed_at::date = %s
                       ORDER BY computed_at DESC LIMIT 1""", (tk, today))
                r = c.fetchone()
            rows[tk] = (tuple(float(v) if i < 5 and v is not None else v
                              for i, v in enumerate(r))
                        if r else (None,) * 7)
        lines = [f"🌅 **Morning gamma board — {today:%a %b %-d}** "
                 f"(the 7:30 sweep; these are the marks the drift "
                 f"alerts measure against)"]
        lines.append("__Venues__")
        lines += [_fmt_row(tk, rows[tk]) for tk in VENUES]
        lines.append("__Mega-caps (eyes only — never armed)__")
        lines += [_fmt_row(tk, rows[tk]) for tk in DRIFT_TICKERS
                  if tk not in VENUES]
        lines.append("_Walls re-price every 15 min through the session; "
                     "material moves ping here as they happen._")
        return claim_and_send(KIND_BOARD, today.isoformat(), CHANNEL,
                              "\n".join(lines), conn=conn)
    finally:
        conn.close()


def run_board_catchup() -> str:
    """Boot pass: if the service starts after 8:05 on a weekday and
    today's board never posted, post it late (the claim makes this
    idempotent). Outside the window it is a clean no-op."""
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo(ET))
    if now.weekday() >= 5:
        return "off"
    if not (dt.time(8, 5) <= now.time() < dt.time(16, 0)):
        return "off"
    return run_board_ping()
