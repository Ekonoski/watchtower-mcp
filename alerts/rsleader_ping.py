"""
The morning RS-leader + flip-proximity pings (2026-08-31, the evening
the studies read out — Eric: "I want both of those pieces of
information sent to my discord in the morning as they happen. There
can't be a 15 minute delay on these, it has to happen at that
candle's close").

Three messages to #desk, each at-most-once per day via
discord_notify_log claims, each read-only over the books:

  🧭 9:31   flip-proximity: SPY/QQQ open distance to the morning
            flip, with the study's crossing prior beside it (n stated
            — small n, measurement not gate).
  🏁 9:45   the RS rank: leader named if it clears the +0.4% bar vs
            QQQV (the graded qualifier), or STAND-ASIDE — zero is
            data. Laggard stated as context only; the short is
            REFUSED (era flip), never suggested as a trade.
  🎯 live   the GO: every minute 9:46-11:00 the leader's just-closed
            1m bar is checked against the STUDY's entry definition —
            first touch-and-hold of the 1m 8/21 (wick rule) — and the
            alert posts seconds after that candle closes, with entry,
            stop, and the graded prior. If no bar qualifies by 11:00,
            a no-trade line posts once — the window closing is data.

One definition, imported never reimplemented: rs_rank, find_go_entry,
ema and the 0.4/9:45/11:00 constants come from analysis.rsleader_study
— the module that GRADED the trade. EMAs are day-anchored from the
9:30 bar exactly as graded (the Scanner chart's continuous EMAs can
differ slightly; the alert trades the graded definition). Prices are
Polygon real-time 1m aggs; a missing feed renders unavailable, never
a guess. The service restarting mid-window is survivable: each pass
re-scans the whole 9:45+ window, so a GO that printed during a restart
still alerts (marked late) with the ORIGINAL bar's entry.
"""
import datetime as dt
import logging

from alerts.discord_notify import claim_and_send
from analysis.rsleader_study import (ENTRY_CUTOFF, MEASURE, RS_MIN, TICKERS,
                                     ema, find_go_entry, rs_rank)

log = logging.getLogger("watchtower.rsleader_ping")

CHANNEL = "desk"
KIND_FLIP = "flipprox_open"
KIND_RANK = "rsl_rank"
KIND_GO = "rsl_go"
KIND_ARM = "rsl_arm"
KIND_EXIT = "rsl_exit"
KIND_BELL = "rsl_bell"
# Eric's manual R (set 2026-09-01, in CLAUDE.md doctrine): changes only
# at a flat, scheduled, market-closed review — edit this line then, and
# never intraday, never after a loss.
R_DOLLARS = 250.0
ET = "America/New_York"

_rank_cache = {}     # date -> (leader, laggard, rs_dict); refetchable


def _today_1m(client, ticker, today):
    """Today's COMPLETED RTH 1m bars, oldest first: [(ts_et, o, h, l,
    c)]. The currently-forming minute is dropped — a partial bar's
    'close' is not a close (the wick rule starts at the data layer)."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo(ET)
    cutoff = dt.datetime.now(ZoneInfo(ET)).replace(second=0, microsecond=0)
    aggs = list(client.get_aggs(ticker, multiplier=1, timespan="minute",
                                from_=today.isoformat(),
                                to=today.isoformat(), limit=1200))
    out = []
    for a in aggs:
        t = dt.datetime.fromtimestamp(a.timestamp / 1000,
                                      dt.timezone.utc).astimezone(et)
        if dt.time(9, 30) <= t.time() <= dt.time(15, 59) and t < cutoff:
            out.append((t, float(a.open), float(a.high), float(a.low),
                        float(a.close)))
    return out


def _rank_now(client, today):
    """The study's 9:45 rank from live 1m bars: returns per-ticker
    return-from-open through the last bar BEFORE 9:45, QQQ the same
    way. None on data holes."""
    rets = {}
    for tk in TICKERS + ("QQQ",):
        bars = _today_1m(client, tk, today)
        if not bars:
            return None
        o930 = bars[0][1]
        px = None
        for ts, o, h, l, c in bars:
            if ts.time() < MEASURE:
                px = c
            else:
                break
        if px is None:
            return None
        rets[tk] = (px / o930 - 1) * 100
    qqq = rets.pop("QQQ")
    return rs_rank(rets, qqq), rets, qqq


def run_flipprox_open_ping() -> str:
    """9:31 ET: SPY/QQQ open vs the morning flip, prior beside it."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo(ET)).date()
    client = get_client()
    conn = _conn()
    try:
        lines = ["🧭 **Flip proximity at the open**"]
        for tk in ("SPY", "QQQ"):
            with conn.cursor() as c:
                c.execute("""SELECT gamma_flip FROM gex_levels
                             WHERE ticker=%s AND computed_at::date=%s
                             ORDER BY computed_at LIMIT 1""", (tk, today))
                r = c.fetchone()
            flip = float(r[0]) if r and r[0] is not None else None
            bars = _today_1m(client, tk, today) if client else []
            if flip is None or not bars:
                lines.append(f"{tk}: *unavailable* "
                             f"({'no board' if flip is None else 'no bars'})")
                continue
            o = bars[0][1]
            dist = abs(o - flip) / o * 100
            with conn.cursor() as c:
                c.execute("""SELECT count(*), round(avg(flip_crosses),1)
                             FROM flipprox_days
                             WHERE flip_px IS NOT NULL AND dist_pct >= %s
                               AND dist_pct < %s""",
                          (0.0 if dist < 0.15 else 0.15 if dist < 0.3
                           else 0.3 if dist < 0.6 else 0.6,
                           0.15 if dist < 0.15 else 0.3 if dist < 0.3
                           else 0.6 if dist < 0.6 else 99.0))
                n, crosses = c.fetchone()
            side = "above" if o > flip else "below"
            lines.append(
                f"{tk}: open {o:.2f}, {dist:.2f}% {side} flip {flip:.2f} — "
                f"days at this distance crossed the flip ~{crosses}x "
                f"(n={n}, small-n read, not a gate)")
        lines.append("_Hugging the flip (<0.3%) = the level gets fought "
                     "over — blender risk; >0.6% away = the flip is not "
                     "today's battleground._")
        return claim_and_send(KIND_FLIP, today.isoformat(), CHANNEL,
                              "\n".join(lines), conn=conn)
    finally:
        conn.close()


def run_rank_ping() -> str:
    """9:45 ET: name the leader or the stand-aside."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo(ET)).date()
    client = get_client()
    if client is None:
        return "off"
    got = _rank_now(client, today)
    conn = _conn()
    try:
        if got is None:
            return claim_and_send(KIND_RANK, today.isoformat(), CHANNEL,
                                  "🏁 RS rank 9:45: *unavailable* (bar feed "
                                  "hole)", conn=conn)
        (leader, laggard, _mid, rs), rets, qqq = got
        _rank_cache.clear()
        _rank_cache[today] = (leader, laggard, rs)
        ordered = sorted(rs, key=lambda t: rs[t], reverse=True)
        board = " · ".join(f"{t} {rs[t]:+.2f}" for t in ordered)
        if leader:
            msg = (f"🏁 **RS leader 9:45: {leader}** "
                   f"({rs[leader]:+.2f}% vs QQQ — clears the +{RS_MIN}% bar)\n"
                   f"{board}\n"
                   f"Watch {leader}'s 1m chart (Scanner 1M column): first "
                   f"8/21 touch that CLOSES holding, 9:45–11:00, is the "
                   f"graded entry — 🎯 alert fires at that candle's close.\n"
                   f"_Prior: +0.45R avg (capped) held to the close, 52% of "
                   f"days positive, n=446, both year-halves, all 7 names. "
                   f"Hold to close — the 2R-target version failed "
                   f"replication._")
        else:
            msg = (f"🏁 RS rank 9:45: **STAND-ASIDE** — no name clears "
                   f"+{RS_MIN}% vs QQQ. Zero is data.\n{board}")
        if laggard:
            msg += (f"\n_Laggard {laggard} ({rs[laggard]:+.2f}%): context "
                    f"only — the short graded REFUSED (era flip)._")
        return claim_and_send(KIND_RANK, today.isoformat(), CHANNEL, msg,
                              conn=conn)
    finally:
        conn.close()


def _find_go(client, today):
    """Recompute today's leader + GO deterministically from the bars.
    Returns ('go', leader, bars, i, entry, stop) | ('none', leader) |
    a status string. Deterministic on the same bars, so the trade
    watcher can rebuild state after any restart."""
    cached = _rank_cache.get(today)
    if cached is None:
        got = _rank_now(client, today)
        if got is None:
            return "hole"
        cached = (got[0][0], got[0][1], got[0][3])
        _rank_cache[today] = cached
    leader = cached[0]
    if leader is None:
        return "stand_aside"
    bars = _today_1m(client, leader, today)
    if len(bars) < 16:
        return "warming"
    closes = [b[4] for b in bars]
    e8, e21 = ema(closes, 8), ema(closes, 21)
    i945 = next((i for i, b in enumerate(bars)
                 if b[0].time() >= MEASURE), None)
    icut = next((i for i, b in enumerate(bars)
                 if b[0].time() >= ENTRY_CUTOFF), len(bars))
    if i945 is None:
        return "warming"
    got = find_go_entry(bars, e8, e21, i945, icut, "long")
    if got is None:
        return ("none", leader)
    i, entry, stop = got
    return ("go", leader, bars, i, entry, stop)


def run_go_watch() -> str:
    """Every minute 9:46–11:00 ET: check the leader's just-closed 1m
    bar for the study's GO entry; post at that candle's close with
    every number precomputed — Eric calculates NOTHING live."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    from zoneinfo import ZoneInfo
    et = ZoneInfo(ET)
    now = dt.datetime.now(et)
    today = now.date()
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM discord_notify_log WHERE kind=%s AND "
                      "ref=%s", (KIND_GO, today.isoformat()))
            if c.fetchone():
                return "done"          # fired (or no-trade posted) already
        client = get_client()
        if client is None:
            return "off"
        res = _find_go(client, today)
        if isinstance(res, str):
            return res
        if res[0] == "go":
            _, leader, bars, i, entry, stop = res
            bar_ts = bars[i][0]
            age = (now - bar_ts).total_seconds() / 60
            late = " *(late alert — bar printed earlier; entry is that "\
                   "bar's close)*" if age > 2.5 else ""
            risk = entry - stop
            arm = entry + risk
            disaster = entry * (1 - 0.01)
            per_ct = 0.70 * risk * 100
            per_ct_atm = 0.55 * risk * 100
            n_itm = int(R_DOLLARS // per_ct) if per_ct > 0 else 0
            n_atm = int(R_DOLLARS // per_ct_atm) if per_ct_atm > 0 else 0
            if n_itm >= 1:
                size_line = (f"**Size for ${R_DOLLARS:.0f} R: "
                             f"{n_itm} contract{'s' if n_itm != 1 else ''} "
                             f"at 0.70Δ ITM · {n_atm} ATM (~0.55Δ).** "
                             f"Round-down applied.")
            else:
                size_line = (f"**SKIP at ${R_DOLLARS:.0f} R** — one 0.70Δ "
                             f"contract risks ~${per_ct:.0f} at the stop. "
                             f"Pass; never tighten the stop to fit.")
            msg = (f"🎯 **GO — {leader}** 1m {bar_ts:%H:%M} candle closed "
                   f"holding the 1m 8/21.{late}\n"
                   f"**Entry {entry:.2f}** · stop level **{stop:.2f}** "
                   f"(5m CLOSE through = out; a touch is not a stop) · "
                   f"disaster **{disaster:.2f}** (touch = out, no waiting)\n"
                   f"**Trail switch at {arm:.2f}** (+1R): from there, out "
                   f"on a 5m CLOSE below the 5m 21 EMA — I'll ping the "
                   f"switch and the exit; you act, don't compute.\n"
                   f"{size_line}\n"
                   f"_(One 0.70Δ contract ≈ ±${per_ct:.0f} at stop/switch — "
                   f"the fallback division if you buy another strike.)_\n"
                   f"_Graded: trail-after-1R, the only exit positive in "
                   f"both year-halves (+0.40/+0.27 avg R, ~40% win, "
                   f"n=377). No profit target._")
            return claim_and_send(KIND_GO, today.isoformat(), CHANNEL, msg,
                                  conn=conn)
        if now.time() >= ENTRY_CUTOFF:
            return claim_and_send(
                KIND_GO, today.isoformat(), CHANNEL,
                f"🎯 {res[1]}: no 1m 8/21 hold printed by 11:00 — window "
                f"closed, NO TRADE today. The no-qualifier day is a "
                f"recorded decision.", conn=conn)
        return "waiting"
    finally:
        conn.close()


def run_trade_watch() -> str:
    """Every minute after a GO until the close: rebuild the trade's
    state from the bars (deterministic — restarts change nothing) and
    ping only on STATE CHANGES: 📈 the +1R trail switch, 🚪 the exit
    with its reason, 🔔 the 3:55 still-in bell reminder. The graded
    lifecycle, executed by the desk; Eric's job is to act on pings."""
    from analysis.hybrid_exit_study import _ema as ema5
    from analysis.hybrid_exit_study import _res5 as res5
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    from zoneinfo import ZoneInfo
    et = ZoneInfo(ET)
    now = dt.datetime.now(et)
    today = now.date()
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT kind FROM discord_notify_log WHERE ref=%s AND "
                      "kind IN (%s,%s,%s,%s)",
                      (today.isoformat(), KIND_GO, KIND_ARM, KIND_EXIT,
                       KIND_BELL))
            kinds = {r[0] for r in c.fetchall()}
        if KIND_GO not in kinds or KIND_EXIT in kinds:
            return "idle"
        client = get_client()
        if client is None:
            return "off"
        res = _find_go(client, today)
        if isinstance(res, str) or res[0] != "go":
            return "hole"              # GO posted but bars disagree; retry
        _, leader, bars, i_go, entry, stop = res
        risk = entry - stop
        if risk <= 0:
            return "hole"
        arm_px = entry + risk
        disaster = entry * (1 - 0.01)
        bars5, last5 = res5(bars)
        e21_5 = ema5([b[4] for b in bars5], 21)
        e21_by_min = {last5[j]: e21_5[j] for j in range(len(bars5))}
        armed = False
        exit_hit = None                # (reason, px)
        stop_now = stop
        for i in range(i_go + 1, len(bars)):
            ts, o, h, l, c = bars[i]
            if l <= disaster:
                exit_hit = ("disaster cap touched", disaster)
                break
            if h >= arm_px:
                armed = True
            e21 = e21_by_min.get(i)
            if e21 is not None:
                if armed and c < e21:
                    exit_hit = ("5m closed below the 21-EMA trail", c)
                    break
                if not armed and c < stop_now:
                    exit_hit = ("5m closed through the stop", c)
                    break
        if exit_hit is not None:
            reason, px = exit_hit
            r = (px - entry) / risk
            msg = (f"🚪 **EXIT — {leader}**: {reason} at {px:.2f} "
                   f"({r:+.2f}R from entry {entry:.2f}). Close the "
                   f"position now. Log it: `watchtower_journal_log`.")
            return claim_and_send(KIND_EXIT, today.isoformat(), CHANNEL,
                                  msg, conn=conn)
        if armed and KIND_ARM not in kinds:
            msg = (f"📈 **{leader} touched +1R ({arm_px:.2f}) — TRAIL "
                   f"LIVE.** From here: out on a 5m CLOSE below the 5m "
                   f"21 EMA (I'll ping it). The fixed stop no longer "
                   f"applies; the disaster cap {disaster:.2f} still does.")
            return claim_and_send(KIND_ARM, today.isoformat(), CHANNEL,
                                  msg, conn=conn)
        if now.time() >= dt.time(15, 55) and KIND_BELL not in kinds:
            msg = (f"🔔 **{leader} still in at 3:55** — exit AT THE CLOSE. "
                   f"The graded exit is the closing print; don't hold "
                   f"overnight.")
            return claim_and_send(KIND_BELL, today.isoformat(), CHANNEL,
                                  msg, conn=conn)
        return "holding"
    finally:
        conn.close()
