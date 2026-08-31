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


def run_go_watch() -> str:
    """Every minute 9:46–11:00 ET: check the leader's just-closed 1m
    bar for the study's GO entry; post at that candle's close."""
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
        if got is not None:
            i, entry, stop = got
            bar_ts = bars[i][0]
            age = (now - bar_ts).total_seconds() / 60
            late = " *(late alert — bar printed earlier; entry is that "\
                   "bar's close)*" if age > 2.5 else ""
            msg = (f"🎯 **GO — {leader}** 1m {bar_ts:%H:%M} candle closed "
                   f"holding the 1m 8/21.{late}\n"
                   f"Entry {entry:.2f} (that close) · stop level {stop:.2f} "
                   f"(under the pullback bar) — exit on a **5m CLOSE** "
                   f"through it, not a touch (graded: the close rule "
                   f"nearly doubled expectancy on leader days, 12.6 vs "
                   f"7.2 bps avg, whipsaw 29% vs 39%, n=377) · disaster "
                   f"cap −1% from entry on touch\n"
                   f"Plan: hold to the close — graded +0.45R avg / 52% "
                   f"positive days (n=446, capped, both halves, 7/7 "
                   f"names); the 2R scalp version failed replication.")
            return claim_and_send(KIND_GO, today.isoformat(), CHANNEL, msg,
                                  conn=conn)
        if now.time() >= ENTRY_CUTOFF:
            return claim_and_send(
                KIND_GO, today.isoformat(), CHANNEL,
                f"🎯 {leader}: no 1m 8/21 hold printed by 11:00 — window "
                f"closed, NO TRADE today. The no-qualifier day is a "
                f"recorded decision.", conn=conn)
        return "waiting"
    finally:
        conn.close()
