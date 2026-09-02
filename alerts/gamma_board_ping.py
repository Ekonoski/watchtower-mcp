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


def _wall_bits(nm, px, strength_side):
    """'CW 765' plus, when the row carries strength (2026-09-02):
    ' (+2.2bn · 41%)' — weight and share of the side's gamma."""
    if px is None:
        return f"{nm} *n/a*"
    txt = f"{nm} {px:.0f}"
    s = strength_side or {}
    if s.get("gex_bn") is not None and s.get("share") is not None:
        txt += f" ({s['gex_bn']:+.1f}bn · {s['share'] * 100:.0f}%)"
    return txt


def _ladder(top_strikes, n=6):
    """The strike ladder (2026-09-02, Eric: 'where the strongest walls
    actually are'): the top-N strikes by |net gamma|, sorted by price,
    puts negative. Empty when the row has none — rendered as a hole."""
    if not top_strikes:
        return ""
    rows = sorted(top_strikes, key=lambda x: abs(float(x["gex_bn"])),
                  reverse=True)[:n]
    rows = sorted(rows, key=lambda x: float(x["strike"]), reverse=True)
    return "  ".join(f"{float(r['strike']):g}:{float(r['gex_bn']):+.1f}"
                     for r in rows)


def _fmt_row(tk, row, strength=None, top_strikes=None):
    """One board line. row = (spot, call_wall, put_wall, gamma_flip,
    net_gex, regime, computed_at_et) with any element possibly None;
    strength = the wall_strength blob; top_strikes = the ladder source."""
    spot, cw, pw, gf, ng, regime, ts = row
    if spot is None:
        return f"{tk}: *unavailable* (no board row)"
    st = strength or {}
    bits = [f"**{tk}** {spot:.2f}"]
    bits.append(_wall_bits("CW", cw, st.get("call")))
    bits.append(_wall_bits("PW", pw, st.get("put")))
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
    lad = _ladder(top_strikes)
    if lad:
        line += f"\n  ladder: {lad}"
    return line


def run_board_ping() -> str:
    """~8:05 ET: the morning board, once per day."""
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    today = dt.datetime.now(ZoneInfo(ET)).date()
    conn = _conn()
    try:
        rows, extra = {}, {}
        for tk in VENUES + tuple(t for t in DRIFT_TICKERS
                                 if t not in VENUES):
            with conn.cursor() as c:
                c.execute(
                    """SELECT spot, call_wall, put_wall, gamma_flip,
                              net_gex, regime,
                              computed_at AT TIME ZONE 'America/New_York',
                              wall_strength, top_strikes
                       FROM gex_levels
                       WHERE ticker = %s AND computed_at::date = %s
                       ORDER BY computed_at DESC LIMIT 1""", (tk, today))
                r = c.fetchone()
            if r:
                rows[tk] = tuple(float(v) if i < 5 and v is not None else v
                                 for i, v in enumerate(r[:7]))
                extra[tk] = (r[7], r[8])
            else:
                rows[tk] = (None,) * 7
                extra[tk] = (None, None)
        lines = [f"🌅 **Morning gamma board — {today:%a %b %-d}** "
                 f"(the 7:30 sweep; these are the marks the drift "
                 f"alerts measure against — walls carry weight · share "
                 f"of side; ladder = top strikes, $bn, puts negative)"]
        lines.append("__Venues__")
        lines += [_fmt_row(tk, rows[tk], *extra[tk]) for tk in VENUES]
        lines.append("__Mega-caps (eyes only — never armed)__")
        lines += [_fmt_row(tk, rows[tk], *extra[tk]) for tk in DRIFT_TICKERS
                  if tk not in VENUES]
        # Touch priors (2026-09-02, the wall-touch study): for each venue
        # level within 3% of this board's spot, how often a level at
        # this distance / regime / kind got touched by the close — n
        # beside every number, holes counted separately, small-n stated.
        try:
            from analysis.wall_touch_study import prior as _prior
            pl = []
            for tk in VENUES:
                spot, cw, pw, gf, ng, regime, ts = rows[tk]
                if spot is None:
                    continue
                parts = []
                for kind, lvl, nm in (("call_wall", cw, "CW"),
                                      ("put_wall", pw, "PW"),
                                      ("gamma_flip", gf, "flip")):
                    if lvl is None or lvl <= 0:
                        continue
                    dist = (spot - lvl) / lvl * 100.0
                    if abs(dist) > 3.0:
                        continue
                    p = _prior(conn, kind, regime, dist)
                    if p is None:
                        parts.append(f"{nm} {abs(dist):.2f}% away: *no prior yet*")
                    else:
                        pct, n, holes = p
                        parts.append(f"{nm} {abs(dist):.2f}% away: touched "
                                     f"{pct:.0f}% (n={n}"
                                     f"{', holes ' + str(holes) if holes else ''})")
                if parts:
                    pl.append(f"{tk}: " + " · ".join(parts))
            if pl:
                lines.append("__Touch priors by the close__ (same kind, "
                             "regime, distance bucket — small-n, record "
                             "since 2026-08-19)")
                lines += pl
        except Exception as e:
            lines.append(f"__Touch priors__: *unavailable* ({e})")
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
