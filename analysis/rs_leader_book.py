"""
The RS-leader audition book (2026-08-31 late — Eric: "Yes go build the
paper book"). One spec per day, trading EXACTLY the graded definition
alongside Eric's own manual execution of the same signal — the book
grades the rule, the journal grades the hand, and the gap between them
is the human-interference measurement he asked for.

The graded definition (rsleader_study + hybrid_exit_study, all
definitions IMPORTED, never reimplemented):
  - 9:45 ET: rank the mag 7 by return-from-open minus QQQ's; the
    leader qualifies at rank 1 with RS >= +0.4%. No qualifier ->
    'skipped_rank' (zero is data). Laggard shorts are NOT traded
    (refused, era flip). Since 2026-09-09 the rank runs over
    LIVE_TICKERS — the graded seven plus the EXPERIMENTAL seat(s),
    tagged everywhere and graded on this book's own n.
  - Entry: the FIRST 1m bar 9:45-11:00 that touches the 1m 8/21 EMA
    and CLOSES holding (wick rule), at that close. No GO by 11:00 ->
    'cancelled' (no_qualifier — a recorded decision).
  - Stop: under the pullback bar (-0.05%), exits on a completed 5m
    CLOSE through; disaster cap -1% from entry on TOUCH.
  - At entry +1R (1m high touch): the trail — exit on a 5m CLOSE
    below the day-anchored 5m 21 EMA (the hybrid study's frame).
  - Survivors exit at the close (eod_flat).
  Prior beside every spec: +0.40/+0.27 avg R by year-half, ~40% win,
  n=377 leader days; caveats — closes as fills, no costs.

  That was v1 (book V1_BOOK, 2026-09-01 → 2026-09-15, 10 specs).
  V2 (2026-09-15, Eric, after the desk's META trade printed +3.0R at
  its high and the trail took it out −2.01R: "you went from a
  profitable trade to a negative two R trade... poor trade management"
  → "build that"): the SAME GO entry, HIS exit — the day trader's
  frame the exit-shape study graded on 2026-09-04 (half at the first
  level + level exits were the only family positive in both year-halves
  in option dollars; the trail was +3.7/+2.0 bps, 35% win):
  - Before the first partial: struct stop on a completed 5m CLOSE and
    the -1% disaster on touch, exactly as v1.
  - TP1 = the nearest level above entry (>= 40 bps away) among PDH,
    premarket high, the 30-minute opening-range high and the pre-GO
    session high — levels within 0.1% of each other merge; a strike-
    grid line is the declared LAST RESORT when no structural level is
    in reach (the graded set's own fallback, graded -$6 / 44% hit as a
    TP1 — stated on the ping). Half off on the TOUCH.
  - The runner: stop to ENTRY on touch (the free trade), then ratcheted
    to each completed 5m block's low (touch-honoured — a resting stop
    order is what he places). TP2 = the next level, rest off on the
    touch; no second level → the runner rides to the bell.
  Stated honestly: the graded cells were half+breakeven-on-touch
  (+7.7/+6.2 bps, 61%, +$24/+$34 per 0.70Δ contract) and half+ratchet
  with breakeven on a 5m CLOSE (+6.9/+6.8, 60%, +$33/+$44); Eric's
  execution — breakeven on touch AND the ratchet — sits between them
  and grades on this book's own n. v1 stops arming; its record stands
  under its own name.

Fill honesty: every decision reads bars PERSISTED to rsl_book_bars
(1m, written as first seen, never revised — reconstruction is not
tape); the tick loop is idempotent and re-derives all state from the
record, so restarts change nothing. Writes ONLY rsl_book_bars and
book=BOOK rows in paper_specs/paper_trades. Promotion gate:
~30 resolved trades, small-n rule beside every number until then.
"""
import datetime as dt
import json
import logging

from analysis.hybrid_exit_study import _ema as ema5
from analysis.hybrid_exit_study import _res5 as res5
from analysis.rsleader_study import (ENTRY_CUTOFF, MEASURE, RS_MIN, TICKERS,
                                     ema, find_go_entry, rs_rank)

log = logging.getLogger("watchtower.rsl_book")

BOOK = "rs_leader_v2"
SETUP = "rsl_go_levels"
V1_BOOK = "rs_leader"          # retired 2026-09-15; the record stands
DISASTER_PCT = 0.01
EOD = dt.time(15, 59)
TP1_FRAC = 0.5
LEVEL_MERGE_PCT = 0.001        # levels within 0.1% of each other are one level
PRIMARY_KINDS = ("pdh", "pmh", "orh", "hod")
# the whole-trade exit reasons once half is banked: what took the RUNNER out
POST_TP1_REASONS = {"tp1_be", "tp1_ratchet", "tp1_tp2", "tp1_eod"}
PRIOR_TXT = ("Prior (exit-shape study, 446 GOs, 2026-09-04): half at the "
             "first naive level + level exits were the only exit family "
             "positive in BOTH year-halves in 0.70Δ option dollars — "
             "half+breakeven-touch +7.7/+6.2 bps, 61% win, +$24/+$34 per "
             "contract; half+5m-low ratchet +6.9/+6.8 bps, 60%; the v1 "
             "trail +3.7/+2.0 bps, 35% win. Closes as fills, no costs; "
             "breakeven-on-touch WITH the ratchet is between two graded "
             "cells and grades on this book's own n.")

# THE LIVE UNIVERSE (2026-09-09, the leader-board seat test — Eric:
# "add PLTR as the 8th name experimental"). rsleader_study.TICKERS is the
# GRADED universe and the seat test's control; it never changes here.
# An EXPERIMENTAL name rides the 9:45 rank beside the seven, TAGGED on
# every post and in every rationale, and is graded on this book's own n
# — the tag-not-gate doctrine. PLTR's own prior as leader in mag7+PLTR:
# positive in both year-halves; the dilution leg was a statistical tie
# (0.35 vs 0.37R, h1, n=231) and Eric ruled the tie in. A name leaves
# this tuple at a flat review, never after a hot or cold week. AMD /
# AVGO / MU / NFLX sign-flipped by half and stay off.
EXPERIMENTAL = ("PLTR",)
EXPERIMENTAL_PRIOR = {
    "PLTR": "+0.58R (n=65, 52%) / +1.07R (n=58, 53%) by year-half as "
            "leader, hold-to-close, closes as fills, no costs — seat test "
            "2026-09-09; the live book grades it on its own n",
}
LIVE_TICKERS = TICKERS + EXPERIMENTAL


def label(ticker: str) -> str:
    """The name as every post and rationale prints it: an experimental
    seat is never rendered as a graded one."""
    return f"{ticker} (experimental)" if ticker in EXPERIMENTAL else ticker


def rank_live(rets_by_ticker: dict, qqq_ret: float):
    """Pure: the 9:45 rank over the LIVE universe, with the mag-7-only
    rank beside it so an experimental leader states whom it displaced.
    rets_by_ticker may omit an experimental name (a bar hole drops the
    seat, never the read — the graded seven are ranked without it).
    Returns (leader, laggard, midpack, rs, displaced) where `displaced`
    is the mag-7 leader the experimental name took the seat from, or
    None."""
    leader, laggard, midpack, rs = rs_rank(rets_by_ticker, qqq_ret)
    displaced = None
    if leader in EXPERIMENTAL:
        seven = {t: r for t, r in rets_by_ticker.items() if t in TICKERS}
        displaced = rs_rank(seven, qqq_ret)[0] if seven else None
    return leader, laggard, midpack, rs, displaced


def lifecycle_state(bars, i_go, entry, stop, *, arm_px=None, trail=True,
                    struct_stop=True):
    """Pure: the live trade's state from persisted 1m bars after the
    GO bar. Returns {'armed': bool, 'exit': (reason, ts, px) | None}.
    Wick rule: stop/trail decide on completed 5m closes; only the
    disaster cap exits on touch.

    The keyword switches exist for RESEARCH (analysis/rsl_exit_study,
    2026-09-02) so exit variants grade through this one definition
    instead of a copy: `struct_stop=False` ignores a 5m close through
    the stop, `trail=False` ignores the trail, `arm_px` overrides the
    +1R switch. Defaults are the live book; the disaster touch is never
    switchable."""
    risk = entry - stop
    if arm_px is None:
        arm_px = entry + risk
    disaster = entry * (1 - DISASTER_PCT)
    bars5, last5 = res5(bars)
    e21_5 = ema5([b[4] for b in bars5], 21)
    # COMPLETED blocks only (2026-09-01, the 14:27 META exit): res5
    # appends the trailing partial block, and mapping its running last
    # bar lets a 1m close mid-block masquerade as a 5m close — the
    # wick-rule violation. A non-trailing block is proven complete by
    # the later block's existence; the trailing one only by its final
    # minute (offset % 5 == 4) having printed.
    e21_by_min = {}
    for j in range(len(bars5)):
        ts_j = bars[last5[j]][0]
        done = (j < len(bars5) - 1
                or ((ts_j.hour - 9) * 60 + ts_j.minute - 30) % 5 == 4)
        if done:
            e21_by_min[last5[j]] = e21_5[j]
    armed = False
    for i in range(i_go + 1, len(bars)):
        ts, o, h, l, c = bars[i]
        if l <= disaster:
            return {"armed": armed, "exit": ("disaster", ts, disaster)}
        if h >= arm_px:
            armed = True
        e21 = e21_by_min.get(i)
        if e21 is not None:
            if trail and armed and c < e21:
                return {"armed": True, "exit": ("trail", ts, c)}
            if struct_stop and not armed and c < stop:
                return {"armed": False, "exit": ("stop", ts, c)}
    return {"armed": armed, "exit": None}


def select_levels(bars, i_go, entry, pdh, pmh):
    """Pure (v2). The take-profit levels frozen at the GO, from the SAME
    level math the exit-shape study graded (naive_levels / pick_targets
    imported): TP1 = the nearest level >= entry * (1 + 40 bps) among PDH,
    premarket high, the 30-minute ORB high and the pre-GO session high;
    TP2 = the next. Levels within LEVEL_MERGE_PCT of each other are ONE
    level (the ORB high and the session high are usually the same print
    — two of the same price would have made TP2 == TP1, an all-off
    wearing a runner's name). A missing PDH / PMH is a named hole. When
    no structural level is in reach the strike-grid line is the declared
    last resort (kind 'strike'), with no TP2. Returns {'tp1': {'px',
    'kind'} | None, 'tp2': ..., 'levels': {kind: px|None}, 'holes': [..],
    'fallback': bool}."""
    # imported here, not at the top: exit_shape_study -> riskmgmt_study
    # -> rs_leader_book.lifecycle_state is an import cycle at module load
    from analysis.exit_shape_study import naive_levels, pick_targets
    lv = naive_levels(bars, i_go, entry, pdh, pmh)
    holes = [k for k in ("pdh", "pmh") if lv.get(k) is None]
    ups = []
    for k in PRIMARY_KINDS:
        px = lv.get(k)
        if px is None:
            continue
        for u in ups:
            if abs(u["price"] - px) <= LEVEL_MERGE_PCT * max(u["price"], px):
                u["kind"] += f"/{k}"
                u["price"] = min(u["price"], px)   # the first print reached
                break
        else:
            ups.append({"price": float(px), "kind": k})
    tp1, tp2 = pick_targets(ups, entry)
    fallback = False
    if tp1 is None:
        t1, _ = pick_targets([{"price": float(lv["strike"]), "kind": "strike"}], entry)
        tp1, tp2, fallback = t1, None, t1 is not None
    out = {"tp1": ({"px": round(tp1["price"], 4), "kind": tp1["kind"]} if tp1 else None),
           "tp2": ({"px": round(tp2["price"], 4), "kind": tp2["kind"]} if tp2 else None),
           "levels": {k: (round(float(v), 4) if v is not None else None) for k, v in lv.items()},
           "holes": holes, "fallback": fallback}
    return out


def describe_levels(lv) -> str:
    """Pure: one line for the rationale, the ping and the log — TP1 with
    its kind (the strike-grid last resort named as such), TP2 or 'no
    TP2', holes named. One sentence in every place the levels render."""
    if not lv or not lv.get("tp1"):
        return ("NO TP1 (no level, no strike — a hole; struct stop / "
                "disaster / bell only)")
    t1 = lv["tp1"]
    s = f"TP1 {t1['px']:.2f} ({t1['kind']}"
    if lv.get("fallback"):
        s += (", strike-grid LAST RESORT — no structural level within reach; "
              "strike TP1s graded −$6 / 44% hit")
    s += ")"
    if lv.get("tp2"):
        s += f", TP2 {lv['tp2']['px']:.2f} ({lv['tp2']['kind']})"
    else:
        s += ", no TP2 — the runner rides to the bell"
    if lv.get("holes"):
        s += f"; level holes: {', '.join(lv['holes'])}"
    return s


def level_inputs(conn, ticker, today):
    """READ ONLY: the prior session's high (daily_prices, the last row
    before today) and today's premarket high (premarket_range — a hole
    when the 9:31/9:41 writer has no row for the name)."""
    with conn.cursor() as c:
        c.execute("""SELECT high FROM daily_prices WHERE ticker=%s AND trade_date<%s
                     AND high IS NOT NULL ORDER BY trade_date DESC LIMIT 1""",
                  (ticker, today))
        r = c.fetchone()
        pdh = float(r[0]) if r and r[0] is not None else None
        c.execute("SELECT pm_high FROM premarket_range WHERE ticker=%s AND trade_date=%s",
                  (ticker, today))
        r = c.fetchone()
        pmh = float(r[0]) if r and r[0] is not None else None
    return pdh, pmh


def _done_block_lows(bars):
    """{index of a completed 5m block's last minute: that block's low}
    — the same completed-block rule as lifecycle_state (a later block
    proves completion; the trailing block needs its final minute)."""
    bars5, last5 = res5(bars)
    out = {}
    for j in range(len(bars5)):
        ts_j = bars[last5[j]][0]
        done = (j < len(bars5) - 1
                or ((ts_j.hour - 9) * 60 + ts_j.minute - 30) % 5 == 4)
        if done:
            out[last5[j]] = bars5[j][3]
    return out


def lifecycle_state_v2(bars, i_go, entry, stop, tp1, tp2=None, *, final=False):
    """Pure (v2, Eric's exit). State of the trade from persisted 1m bars
    after the GO bar. Returns {'legs': [(frac, px, ts, why)], 'tp1_hit',
    'stop': the live stop level, 'stop_mode': 'close' | 'touch',
    'exit': (reason, ts, weighted_px) | None}.

    Order inside a bar: the runner's resting stop (touch) once it exists,
    the disaster touch, TP1 touch (half off, stop -> entry), TP2 touch
    (rest off); then, at a completed 5m block's last minute, the
    pre-TP1 struct stop on the CLOSE (wick rule) or the post-TP1 ratchet
    of the runner's stop to the block's low. `final=True` (the bell)
    closes whatever is left at the last bar's close. The runner's stop is
    checked BEFORE the disaster because it always sits at/above entry,
    above the -1% line — a bar through both was stopped at the higher
    price first."""
    disaster = entry * (1 - DISASTER_PCT)
    blocks = _done_block_lows(bars)
    legs, remaining = [], 1.0
    tp1_hit, rstop = False, None
    reason = None
    for i in range(i_go + 1, len(bars)):
        ts, o, h, l, c = bars[i]
        if tp1_hit and rstop is not None and l <= rstop:
            legs.append((remaining, rstop, ts, "runner_stop"))
            reason = "tp1_be" if rstop <= entry + 1e-9 else "tp1_ratchet"
            remaining = 0.0
            break
        if l <= disaster:
            legs.append((remaining, disaster, ts, "disaster"))
            reason = "disaster"
            remaining = 0.0
            break
        if not tp1_hit and tp1 is not None and h >= tp1:
            frac = min(TP1_FRAC, remaining)
            legs.append((frac, tp1, ts, "tp1"))
            remaining -= frac
            tp1_hit, rstop = True, entry
        if tp1_hit and tp2 is not None and remaining > 0 and h >= tp2:
            legs.append((remaining, tp2, ts, "tp2"))
            reason = "tp1_tp2"
            remaining = 0.0
            break
        lo5 = blocks.get(i)
        if lo5 is not None:
            if not tp1_hit and c < stop:
                legs.append((remaining, c, ts, "stop"))
                reason = "stop"
                remaining = 0.0
                break
            if tp1_hit:
                rstop = max(rstop, lo5)
    if remaining > 0 and final and len(bars) > i_go + 1:
        ts, _o, _h, _l, c = bars[-1]
        legs.append((remaining, c, ts, "bell"))
        reason = "tp1_eod" if tp1_hit else "eod_flat"
        remaining = 0.0
    exit_ = None
    if remaining <= 0 and legs:
        px = sum(f * p for f, p, _t, _w in legs)
        exit_ = (reason, legs[-1][2], round(px, 4))
    return {"legs": legs, "tp1_hit": tp1_hit,
            "stop": (rstop if tp1_hit else stop),
            "stop_mode": ("touch" if tp1_hit else "close"),
            "exit": exit_}


def legs_json(legs):
    return [{"frac": round(f, 4), "px": round(p, 4), "ts": t.isoformat(), "why": w}
            for f, p, t, w in legs]


def _persist_1m(conn, ticker, today):
    """Fetch today's 1m bars and persist NEW ones (first-seen wins);
    return the persisted series oldest-first."""
    from zoneinfo import ZoneInfo
    from analysis.polygon_data import get_client
    et = ZoneInfo("America/New_York")
    client = get_client()
    if client is not None:
        cutoff = dt.datetime.now(et).replace(second=0, microsecond=0)
        try:
            aggs = list(client.get_aggs(ticker, multiplier=1,
                                        timespan="minute",
                                        from_=today.isoformat(),
                                        to=today.isoformat(), limit=1200))
            rows = []
            for a in aggs:
                t = dt.datetime.fromtimestamp(a.timestamp / 1000,
                                              dt.timezone.utc).astimezone(et)
                if dt.time(9, 30) <= t.time() <= EOD and t < cutoff:
                    rows.append((ticker, t, t.date(), float(a.open),
                                 float(a.high), float(a.low),
                                 float(a.close),
                                 float(a.volume) if a.volume is not None
                                 else None))
            if rows:
                with conn.cursor() as c:
                    c.executemany(
                        """INSERT INTO rsl_book_bars
                           (ticker, ts, trade_date, open, high, low,
                            close, volume)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (ticker, ts) DO NOTHING""", rows)
                conn.commit()
        except Exception as e:
            log.warning(f"[rsl-book] {ticker} 1m fetch failed: {e}")
    with conn.cursor() as c:
        c.execute("""SELECT ts, open, high, low, close FROM rsl_book_bars
                     WHERE ticker=%s AND trade_date=%s ORDER BY ts""",
                  (ticker, today))
        return [(ts.astimezone(et), float(o), float(h), float(l), float(cl))
                for ts, o, h, l, cl in c.fetchall()]


def run_rsl_tick():
    """Per-minute pass 9:46-16:01 ET. Idempotent; every transition
    re-derives from rsl_book_bars + the spec/trade rows."""
    from analysis.paper_trader import ET as _ET, get_db_connection
    now = dt.datetime.now(_ET)
    if now.weekday() >= 5 or now.time() < dt.time(9, 46):
        return
    today = now.date()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, ticker, status, stop, levels FROM paper_specs
                         WHERE book=%s AND trade_date=%s""", (BOOK, today))
            spec = c.fetchone()

        if spec is None:
            # 9:45 rank from persisted bars: the live universe + QQQ.
            # A hole in a GRADED name or QQQ = no read (retry next tick);
            # a hole in an EXPERIMENTAL name drops the seat for the day
            # and says so — the seven's read never waits on the eighth.
            rets, holes = {}, []
            for tk in LIVE_TICKERS + ("QQQ",):
                bars = _persist_1m(conn, tk, today)
                o930 = bars[0][1] if bars else None
                px = None
                for b in bars:
                    if b[0].time() < MEASURE:
                        px = b[4]
                    else:
                        break
                if px is None:
                    if tk in EXPERIMENTAL:
                        holes.append(tk)
                        continue
                    return                       # feed hole; retry next tick
                rets[tk] = (px / o930 - 1) * 100
            qqq = rets.pop("QQQ")
            leader, _laggard, _mid, rs, displaced = rank_live(rets, qqq)
            hole_txt = (" " + ", ".join(f"{t} bar hole — ranked without "
                                        f"it" for t in holes) + "."
                        if holes else "")
            board = ", ".join(f"{label(t)} {rs[t]:+.2f}" for t in
                              sorted(rs, key=rs.get, reverse=True))
            if leader is None:
                with conn.cursor() as c:
                    c.execute("""INSERT INTO paper_specs
                        (trade_date, book, ticker, direction, setup,
                         entry_trigger, stop, target, r_dollars, status,
                         rationale, source)
                        VALUES (%s,%s,'—','long',%s,0,0,0,100,
                                'skipped_rank',%s,'rsleader_study')
                        ON CONFLICT DO NOTHING""",
                        (today, BOOK, SETUP,
                         f"{SETUP}: no name cleared +{RS_MIN}% vs QQQ at "
                         f"9:45 — stand-aside (zero is data). Board: "
                         f"{board}.{hole_txt}"))
                conn.commit()
                log.info(f"[rsl-book] {today}: skipped_rank")
                return
            ref = rets[leader]
            if leader in EXPERIMENTAL:
                prior_txt = (f"EXPERIMENTAL SEAT: {leader}'s own prior "
                             f"{EXPERIMENTAL_PRIOR[leader]}. Exit lifecycle "
                             f"is the same v2 level exit as the seven. "
                             + (f"Displaced mag-7 leader {displaced}."
                                if displaced else
                                "The mag-7 alone would have stood aside."))
            else:
                prior_txt = PRIOR_TXT
            with conn.cursor() as c:
                c.execute("""INSERT INTO paper_specs
                    (trade_date, book, ticker, direction, setup,
                     entry_trigger, stop, target, r_dollars, status,
                     rationale, source)
                    VALUES (%s,%s,%s,'long',%s,%s,%s,%s,100,'armed',%s,
                            'rsleader_study')
                    ON CONFLICT DO NOTHING""",
                    (today, BOOK, leader, SETUP, 0, 0, 0,
                     f"{SETUP}: leader {label(leader)} {ref:+.2f}% vs QQQ "
                     f"(bar +{RS_MIN}%). Entry = first 1m 8/21 hold "
                     f"9:45-11:00 at its close; stop under the pullback "
                     f"bar on 5m CLOSES; -1% disaster on touch — both "
                     f"until the first partial. TP1 = nearest level >= "
                     f"40 bps above entry (PDH / premarket high / ORB "
                     f"high / pre-GO session high; strike grid only as a "
                     f"named last resort): HALF off on touch, runner's "
                     f"stop to entry on touch, ratcheted under each "
                     f"completed 5m low; TP2 = the next level, rest off "
                     f"on touch; no TP2 -> the runner rides to the bell. "
                     f"{prior_txt} Board: {board}.{hole_txt} "
                     f"entry_trigger/stop/target are 0 until the GO sets "
                     f"them; the levels freeze in `levels` at the GO."))
            conn.commit()
            log.info(f"[rsl-book] {today}: armed on {label(leader)}")
            return

        sid, ticker, status, stop_db, levels_db = spec
        if status in ("skipped_rank", "cancelled"):
            return
        bars = _persist_1m(conn, ticker, today)
        if len(bars) < 16:
            return
        with conn.cursor() as c:
            c.execute("""SELECT id, entered_at, entry_px, exited_at, legs
                         FROM paper_trades WHERE spec_id=%s""", (sid,))
            trade = c.fetchone()

        if trade is None and status == "armed":
            closes = [b[4] for b in bars]
            e8, e21 = ema(closes, 8), ema(closes, 21)
            i945 = next((i for i, b in enumerate(bars)
                         if b[0].time() >= MEASURE), None)
            icut = next((i for i, b in enumerate(bars)
                         if b[0].time() >= ENTRY_CUTOFF), len(bars))
            if i945 is None:
                return
            got = find_go_entry(bars, e8, e21, i945, icut, "long")
            if got is not None:
                i, entry, stop = got
                pdh, pmh = level_inputs(conn, ticker, today)
                lv = select_levels(bars, i, entry, pdh, pmh)
                # TP1 is a REAL target now (the poller is excluded for
                # this book by name); 999999 stays the sentinel only for
                # the impossible no-level row — never a plausible price
                # (the 2026-09-01 phantom).
                target = lv["tp1"]["px"] if lv["tp1"] else 999999
                tp_txt = f" | GO {bars[i][0]:%H:%M}: " + describe_levels(lv)
                with conn.cursor() as c:
                    c.execute("""INSERT INTO paper_trades
                        (spec_id, entered_at, entry_px, fill_kind,
                         confirm_status)
                        VALUES (%s,%s,%s,'close','n/a')""",
                        (sid, bars[i][0], round(entry, 4)))
                    c.execute("""UPDATE paper_specs SET status='triggered',
                                 entry_trigger=%s, stop=%s, target=%s,
                                 levels=%s::jsonb,
                                 rationale = rationale || %s
                                 WHERE id=%s""",
                              (round(entry, 4), round(stop, 4), target,
                               json.dumps(lv), tp_txt, sid))
                conn.commit()
                log.info(f"[rsl-book] ENTER {ticker} @ {entry:.4f} "
                         f"({bars[i][0]}){tp_txt}")
                return
            if now.time() >= ENTRY_CUTOFF:
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_specs SET status='cancelled',
                        rationale = rationale ||
                        ' | no 1m 8/21 hold by 11:00 — no trade (recorded '
                        'decision)' WHERE id=%s""", (sid,))
                conn.commit()
                log.info(f"[rsl-book] {today}: cancelled (no qualifier)")
            return

        if trade is not None and trade[3] is None:
            tid, ent_at, entry_px, _, legs_db = trade
            entry_px = float(entry_px)
            stop_lvl = float(stop_db)
            if stop_lvl <= 0:
                return                            # inconsistent row; hole
            i_go = next((i for i, b in enumerate(bars)
                         if b[0] >= ent_at.astimezone(bars[0][0].tzinfo)),
                        None)
            if i_go is None:
                return
            lv = levels_db or {}
            tp1 = lv["tp1"]["px"] if lv.get("tp1") else None
            tp2 = lv["tp2"]["px"] if lv.get("tp2") else None
            final = now.time() >= dt.time(16, 0)
            st = lifecycle_state_v2(bars, i_go, entry_px, stop_lvl, tp1, tp2,
                                    final=final)
            risk = entry_px - stop_lvl
            legs = legs_json(st["legs"])
            if st["exit"] is not None:
                reason, ts, px = st["exit"]
                r = (px - entry_px) / risk if risk > 0 else None
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_trades SET exited_at=%s,
                                 exit_px=%s, exit_reason=%s, r_multiple=%s,
                                 legs=%s::jsonb
                                 WHERE id=%s AND exited_at IS NULL""",
                              (ts, round(px, 4), reason, r, json.dumps(legs),
                               tid))
                conn.commit()
                log.info(f"[rsl-book] EXIT {ticker} {reason} @ {px:.4f} "
                         f"legs={legs}")
            elif legs and legs != (legs_db or []):
                # the partial is on the record the minute it prints,
                # before the runner resolves (a half banked is a fill)
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_trades SET legs=%s::jsonb
                                 WHERE id=%s AND exited_at IS NULL""",
                              (json.dumps(legs), tid))
                conn.commit()
                log.info(f"[rsl-book] PARTIAL {ticker} legs={legs} runner "
                         f"stop {st['stop']:.4f} ({st['stop_mode']})")
    finally:
        conn.close()
