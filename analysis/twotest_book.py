"""
The two-test audition book (2026-09-17 — Eric, after his third correct
PLTR skip in a row and the replay that showed his preferred entry
would have won both days: "build the two-test book").

The trade is the one he executes by hand and the one the record graded
on 2026-09-12 (analysis/twotest_study.py, the workbook's "Intraday
Level Trading" engine): on the day's RS LEADER, a completed 5m close
through PDH or the premarket high, then on the 1m a first retest low
L1, a bounce high H, a higher low L2 at-or-above the level — enter on
the cross of H. On leader days the PDH two-test paid +33.6 / +21.4 bps
by half (n=39/45) and the PMH +12.8 / +17.5 (n=51/59) under the
5m-close stop, roughly twice the GO's +13 bps/day on the same
selection; the ORB family was flat on leader days and is NOT traded.
Small n, stated; closes as fills, no costs.

ONE DEFINITION, imported never reimplemented:
  - the leader: the rs_leader_v2 spec's ticker (the 9:45 rank the GO
    book already wrote — the two books trade the same name by
    construction; a v2 stand-aside is a two-test stand-aside).
  - the entry machine: twotest_study.two_test + tapeentry_study's
    level_machine / resample5, with the study's own clocks
    (confirmation 9:35–14:30, trigger by 15:00) and STOP_BUFF.
  - the levels: rs_leader_book.level_inputs (PDH from daily_prices,
    PMH from premarket_range; a missing one is a named hole).
  - the exit: rs_leader_book.lifecycle_state_v2 — Eric's exit as
    ruled on 9/15 (struct stop on a completed 5m CLOSE under L2 and
    the -1% disaster until the first partial; half at TP1, runner to
    entry on touch, ratcheted under 5m lows, TP2 or the bell) with
    the targets from select_levels.

LIVE-VS-STUDY, declared: (1) the study read full days, so every 5m
bar it saw was complete; live, the trailing 5m block is PARTIAL and
its running close is not a close (the 2026-09-01 lesson) — the
confirmation and kill legs here read COMPLETED blocks only
(`completed5`); the 1m legs read every persisted 1m bar (the forming
minute is never persisted). (2) The study filled at H's high; live, a
trigger bar that OPENS above H fills at its open (a price that
printed — the ACVA gap rule). (3) The study's graded expression was
hold-to-the-bell with the 5m-close stop; the book trades Eric's level
exit and records the graded expression as a SHADOW on every trade
(`paper_trades.shadow`, computed from the same recorded bars at the
bell), so the exit choice grades itself.

One spec per day, one trade per day (the first level to trigger wins;
the other family's verdict is recorded). Fill honesty as the GO book:
every decision reads bars PERSISTED to rsl_book_bars; idempotent
ticks; writes only BOOK rows in paper_specs / paper_trades (plus the
bars). Gate: ~30 resolved, small-n rule until then.
"""
import datetime as dt
import json
import logging

from analysis.fills_audit import record_entry, record_exit
from analysis.rs_leader_book import BOOK as RSL_BOOK
from analysis.rs_leader_book import (_persist_1m, describe_levels, label,
                                     legs_json, level_inputs,
                                     lifecycle_state_v2, select_levels)
from analysis.tapeentry_study import level_machine, resample5
from analysis.twotest_study import (CONFIRM_END, CONFIRM_START, STOP_BUFF,
                                    TRIGGER_END, two_test)

log = logging.getLogger("watchtower.twotest_book")

BOOK = "two_test"
SETUP = "tt_leader_level"
FAMILIES = ("pdh", "pmh")          # longs only; ORB flat on leader days, shorts retired
PRIOR_TXT = ("Prior (two-test study, 2026-09-12, leader days, 5m-close stop, "
             "bps to the close): PDH +33.6/+21.4 by half (n=39/45), PMH "
             "+12.8/+17.5 (n=51/59); ORB flat and not traded. Wick-cross "
             "fills, no costs, small n. Exit = Eric's level exit (9/15 "
             "ruling); the graded hold-to-bell rides as a shadow on every "
             "trade.")


# ── pure cores ───────────────────────────────────────────────────────

def completed5(bars):
    """Fixed-anchor 5m blocks of the day's 1m bars, COMPLETED ones only:
    a non-trailing block is proven complete by the next block's
    existence; the trailing block only by its final minute (offset
    % 5 == 4) having printed. Returns (bars5, last1m) like resample5."""
    bars5, last1m = resample5(bars)
    if bars5:
        ts = bars[last1m[-1]][0]
        if ((ts.hour - 9) * 60 + ts.minute - 30) % 5 != 4:
            bars5, last1m = bars5[:-1], last1m[:-1]
    return bars5, last1m


def level_state(bars, level):
    """Pure. One level's state on the day so far, through the study's
    machine on completed 5m blocks + every 1m bar. Returns {'status':
    no_level | no_confirm | confirmed | forming | triggered |
    structure_failed | no_trigger, 'why', 'confirm_ts', 'l1', 'h', 'l2',
    'i_trig', 'entry', 'stop'}."""
    if level is None:
        return {"status": "no_level"}
    if len(bars) < 6:
        return {"status": "no_confirm"}
    bars5, last1m = completed5(bars)
    if not bars5:
        return {"status": "no_confirm"}
    i5_start = next((k for k, b in enumerate(bars5) if b[0].time() >= CONFIRM_START), len(bars5))
    i5_end = next((k for k, b in enumerate(bars5) if b[0].time() > CONFIRM_END), len(bars5))
    i1_cut = next((i for i, b in enumerate(bars) if b[0].time() > TRIGGER_END), len(bars)) - 1
    brk, _ = level_machine(bars5, level, "long", i5_start, i5_end, start_inside=False)
    if brk is None:
        return {"status": "no_confirm"}
    out = {"confirm_ts": bars5[brk][0], "l1": None, "h": None, "l2": None}
    i_from = last1m[brk] + 1
    if i_from >= len(bars):
        out["status"] = "confirmed"
        return out
    tt = two_test(bars, level, "long", i_from, i1_cut, bars5, last1m, brk)
    for k in ("l1", "h", "l2"):
        out[k] = tt[k][1] if tt.get(k) else None
    if tt["status"] == "triggered":
        j = tt["i_trig"]
        h = tt["h"][1]
        out.update({"status": "triggered", "i_trig": j,
                    # the study fills at H; a bar that OPENED above H
                    # fills at its open — a price that printed
                    "entry": round(max(h, bars[j][1]), 4),
                    "stop": round(tt["l2"][1] * (1 - STOP_BUFF), 4),
                    "gap_fill": bars[j][1] > h})
        return out
    if tt["status"] == "structure_failed":
        out.update({"status": "structure_failed", "why": tt["why"]})
        return out
    # no trigger yet: forming while the clock allows, no_trigger after
    out["status"] = "forming" if bars[-1][0].time() <= TRIGGER_END else "no_trigger"
    return out


def day_state(bars, pdh, pmh):
    """Pure. Both families' states and the winning trigger (the earliest
    trigger bar; ties go to PDH, the better-graded level)."""
    fam = {"pdh": level_state(bars, pdh), "pmh": level_state(bars, pmh)}
    trig = None
    for name in FAMILIES:
        st = fam[name]
        if st["status"] == "triggered" and (trig is None or st["i_trig"] < trig["i_trig"]):
            trig = dict(st, family=name)
    return {"families": fam, "trigger": trig}


def verdict_text(fam) -> str:
    """One line per family for the rationale / the cancel row."""
    bits = []
    for name in FAMILIES:
        st = fam[name]
        s = f"{name.upper()}: {st['status']}"
        if st.get("why"):
            s += f" ({st['why']})"
        if st.get("confirm_ts"):
            s += f", confirmed {st['confirm_ts']:%H:%M}"
        bits.append(s)
    return "; ".join(bits)


def shadow_graded(bars, i_trig, entry, stop):
    """The study's graded expression on the same recorded bars: the
    5m-close stop under L2 (+ the -1% disaster) and otherwise the bell
    — lifecycle_state_v2 with no targets, final. Returns {'exit_px',
    'reason', 'r'} or None (no bars after the trigger)."""
    st = lifecycle_state_v2(bars, i_trig, entry, stop, None, None, final=True)
    if st["exit"] is None:
        return None
    reason, ts, px = st["exit"]
    risk = entry - stop
    return {"exit_px": px, "reason": reason, "ts": ts.isoformat(),
            "r": round((px - entry) / risk, 4) if risk > 0 else None}


# ── the tick ─────────────────────────────────────────────────────────

def run_tt_tick():
    """Per-minute pass 9:47–16:01 ET, after the GO book's tick (which
    writes the leader spec at 9:46:30). Idempotent; every transition
    re-derives from rsl_book_bars + the spec/trade rows."""
    from analysis.paper_trader import ET as _ET, get_db_connection
    now = dt.datetime.now(_ET)
    if now.weekday() >= 5 or now.time() < dt.time(9, 47):
        return
    today = now.date()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, ticker, status, stop, levels FROM paper_specs
                         WHERE book=%s AND trade_date=%s""", (BOOK, today))
            spec = c.fetchone()

        if spec is None:
            with conn.cursor() as c:
                c.execute("""SELECT ticker, status FROM paper_specs
                             WHERE book=%s AND trade_date=%s""", (RSL_BOOK, today))
                lead = c.fetchone()
            if lead is None:
                return                        # the leader is not ranked yet; retry
            ticker, lstatus = lead
            if lstatus == "skipped_rank":
                with conn.cursor() as c:
                    c.execute("""INSERT INTO paper_specs
                        (trade_date, book, ticker, direction, setup,
                         entry_trigger, stop, target, r_dollars, status,
                         rationale, source)
                        VALUES (%s,%s,'—','long',%s,0,0,0,100,'skipped_rank',%s,
                                'twotest_study') ON CONFLICT DO NOTHING""",
                        (today, BOOK, SETUP,
                         f"{SETUP}: the GO book stood aside at 9:45 (no leader "
                         f"cleared the bar) — no leader, no two-test (zero is data)."))
                conn.commit()
                log.info(f"[tt-book] {today}: skipped_rank (no leader)")
                return
            pdh, pmh = level_inputs(conn, ticker, today)
            holes = [k for k, v in (("pdh", pdh), ("pmh", pmh)) if v is None]
            lv = {"pdh": pdh, "pmh": pmh, "holes": holes}
            with conn.cursor() as c:
                c.execute("""INSERT INTO paper_specs
                    (trade_date, book, ticker, direction, setup,
                     entry_trigger, stop, target, r_dollars, status,
                     rationale, source, levels)
                    VALUES (%s,%s,%s,'long',%s,0,0,0,100,'armed',%s,
                            'twotest_study',%s::jsonb) ON CONFLICT DO NOTHING""",
                    (today, BOOK, ticker, SETUP,
                     f"{SETUP}: leader {label(ticker)} (the GO book's 9:45 rank). "
                     f"Levels PDH {pdh if pdh is not None else 'hole'} / PMH "
                     f"{pmh if pmh is not None else 'hole'}. Entry = a completed 5m "
                     f"CLOSE through the level (9:35-14:30), then on the 1m a "
                     f"retest low L1, bounce high H, higher low L2 at/above the "
                     f"level; fill on the cross of H (at H, or the open if the "
                     f"bar gapped through), by 15:00. Stop = L2 - 0.05% on a "
                     f"completed 5m CLOSE; -1% disaster on touch; both until the "
                     f"first partial. Then Eric's exit: half at TP1 (nearest level "
                     f">= 40 bps above entry), runner to entry on touch, ratcheted "
                     f"under 5m lows, TP2 or the bell. {PRIOR_TXT} "
                     f"entry_trigger/stop/target are 0 until the trigger sets "
                     f"them." + (f" Level holes: {', '.join(holes)}." if holes else ""),
                     json.dumps(lv)))
            conn.commit()
            log.info(f"[tt-book] {today}: armed on {label(ticker)} pdh={pdh} pmh={pmh}")
            return

        sid, ticker, status, stop_db, levels_db = spec
        if status in ("skipped_rank", "cancelled"):
            return
        lv = levels_db or {}
        bars = _persist_1m(conn, ticker, today)
        if len(bars) < 6:
            return
        with conn.cursor() as c:
            c.execute("""SELECT id, entered_at, entry_px, exited_at, legs, shadow
                         FROM paper_trades WHERE spec_id=%s""", (sid,))
            trade = c.fetchone()

        if trade is None and status == "armed":
            st = day_state(bars, lv.get("pdh"), lv.get("pmh"))
            trig = st["trigger"]
            if trig is not None:
                j, entry, stop = trig["i_trig"], trig["entry"], trig["stop"]
                tp = select_levels(bars, j, entry, lv.get("pdh"), lv.get("pmh"))
                target = tp["tp1"]["px"] if tp["tp1"] else 999999
                lv2 = dict(lv, family=trig["family"], confirm_ts=trig["confirm_ts"].isoformat(),
                           l1=trig["l1"], h=trig["h"], l2=trig["l2"], gap_fill=trig["gap_fill"],
                           tp1=tp["tp1"], tp2=tp["tp2"], tp_levels=tp["levels"],
                           tp_holes=tp["holes"], fallback=tp["fallback"])
                txt = (f" | TRIGGER {bars[j][0]:%H:%M} on {trig['family'].upper()} "
                       f"(confirmed {trig['confirm_ts']:%H:%M}; L1 {trig['l1']:.2f}, "
                       f"H {trig['h']:.2f}, L2 {trig['l2']:.2f}): entry {entry:.2f}"
                       + (" at the OPEN — the bar gapped through H" if trig["gap_fill"] else "")
                       + f", stop {stop:.2f}; " + describe_levels(tp))
                with conn.cursor() as c:
                    c.execute("""INSERT INTO paper_trades
                        (spec_id, entered_at, entry_px, fill_kind, confirm_status)
                        VALUES (%s,%s,%s,'cross','n/a')
                        RETURNING id""",
                        (sid, bars[j][0], round(entry, 4)))
                    tid_new = c.fetchone()[0]
                    c.execute("""UPDATE paper_specs SET status='triggered',
                                 entry_trigger=%s, stop=%s, target=%s,
                                 setup=%s, levels=%s::jsonb,
                                 rationale = rationale || %s WHERE id=%s""",
                              (round(entry, 4), round(stop, 4), target,
                               f"tt_{trig['family']}", json.dumps(lv2), txt, sid))
                    tb = bars[j]
                    record_entry(c, tid_new, BOOK, ticker, round(entry, 4),
                                 fill_kind="cross", expected_px=trig["h"],
                                 gap_through=bool(trig["gap_fill"]),
                                 bar={"ts": tb[0].isoformat(), "open": tb[1],
                                      "high": tb[2], "low": tb[3],
                                      "close": tb[4]},
                                 evidence={"family": trig["family"],
                                           "h": trig["h"], "l2": trig["l2"],
                                           "gap_fill": trig["gap_fill"]})
                conn.commit()
                log.info(f"[tt-book] ENTER {ticker} @ {entry:.4f} ({bars[j][0]}){txt}")
                return
            fam = st["families"]
            dead = all(fam[n]["status"] in ("structure_failed", "no_trigger", "no_level")
                       for n in FAMILIES)
            if now.time() > TRIGGER_END or dead:
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_specs SET status='cancelled',
                                 rationale = rationale || %s WHERE id=%s""",
                              (f" | no two-test trigger — {verdict_text(fam)} "
                               f"(recorded decision)", sid))
                conn.commit()
                log.info(f"[tt-book] {today}: cancelled — {verdict_text(fam)}")
            return

        if trade is None:
            return
        tid, ent_at, entry_px, exited_at, legs_db, shadow_db = trade
        entry_px = float(entry_px)
        stop_lvl = float(stop_db)
        if stop_lvl <= 0:
            return
        i_trig = next((i for i, b in enumerate(bars)
                       if b[0] >= ent_at.astimezone(bars[0][0].tzinfo)), None)
        if i_trig is None:
            return
        final = now.time() >= dt.time(16, 0)
        if exited_at is None:
            tp1 = lv["tp1"]["px"] if lv.get("tp1") else None
            tp2 = lv["tp2"]["px"] if lv.get("tp2") else None
            st = lifecycle_state_v2(bars, i_trig, entry_px, stop_lvl, tp1, tp2, final=final)
            risk = entry_px - stop_lvl
            legs = legs_json(st["legs"])
            if st["exit"] is not None:
                reason, ts, px = st["exit"]
                r = (px - entry_px) / risk if risk > 0 else None
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_trades SET exited_at=%s, exit_px=%s,
                                 exit_reason=%s, r_multiple=%s, legs=%s::jsonb
                                 WHERE id=%s AND exited_at IS NULL""",
                              (ts, round(px, 4), reason, r, json.dumps(legs), tid))
                    record_exit(c, tid, BOOK, ticker, round(px, 4),
                                expected_px=round(stop_lvl, 4) if reason in
                                ("stop", "disaster") else None,
                                evidence={"exit_reason": reason})
                conn.commit()
                log.info(f"[tt-book] EXIT {ticker} {reason} @ {px:.4f} legs={legs}")
            elif legs and legs != (legs_db or []):
                with conn.cursor() as c:
                    c.execute("""UPDATE paper_trades SET legs=%s::jsonb
                                 WHERE id=%s AND exited_at IS NULL""",
                              (json.dumps(legs), tid))
                conn.commit()
                log.info(f"[tt-book] PARTIAL {ticker} legs={legs} runner stop "
                         f"{st['stop']:.4f} ({st['stop_mode']})")
        if final and shadow_db is None:
            # the graded expression, from the same recorded bars, once the
            # day is complete — never before (the bell is part of it)
            sh = shadow_graded(bars, i_trig, entry_px, stop_lvl)
            if sh is not None:
                with conn.cursor() as c:
                    c.execute("UPDATE paper_trades SET shadow=%s::jsonb WHERE id=%s",
                              (json.dumps({"graded_eod": sh}), tid))
                conn.commit()
                log.info(f"[tt-book] SHADOW {ticker} graded_eod={sh}")
    finally:
        conn.close()
