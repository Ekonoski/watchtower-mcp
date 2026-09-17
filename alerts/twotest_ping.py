"""
The two-test pings (2026-09-17 — the book Eric trades by hand, now
narrated by the desk in real time). Read-only over the books; every
state is rebuilt from live 1m bars through the BOOK's own pure
functions (twotest_book.day_state, the v2 lifecycle inside it), so a
ping and the ledger describe the same trade. At-most-once per (kind,
ref) via discord_notify_log claims.

  🧱 tt_confirm   a level (PDH / PMH) confirmed on a completed 5m CLOSE
                  — "watch the 1m for L1 / H / L2". Once per level/day.
  🪜 tt_go        the trigger: the cross of H. Entry, stop (L2 − 0.05%,
                  5m CLOSE rule), TP1 / TP2 with their kind and R, the
                  size for $250 of risk, the graded prior. Seconds
                  after the bar closes.
  💰 tt_tp1 / 🔒 tt_ratchet / 🚪 tt_exit / 🔔 tt_bell — the same
                  lifecycle pings as the GO book, on this trade.
  ✋ tt_done      no trigger by 15:00 — the per-level verdict (a
                  structure that failed is a correct pass; the
                  workbook wants it counted).
"""
import datetime as dt
import logging

from alerts.discord_notify import claim_and_send
from alerts.rsleader_ping import ET, R_DOLLARS, _today_1m
# ONE DEFINITION: the lifecycle is the book's (never a copy here)
from analysis.rs_leader_book import lifecycle_state_v2
from analysis.rs_leader_book import (describe_levels, label, level_inputs,
                                     select_levels)
from analysis.twotest_book import (BOOK, FAMILIES, PRIOR_TXT, day_state,
                                   verdict_text)
from analysis.twotest_study import TRIGGER_END

log = logging.getLogger("watchtower.twotest_ping")

CHANNEL = "desk"
KIND_CONFIRM = "tt_confirm"    # ref = date:family
KIND_GO = "tt_go"
KIND_TP1 = "tt_tp1"
KIND_RATCHET = "tt_ratchet"    # ref = date:px
KIND_EXIT = "tt_exit"
KIND_BELL = "tt_bell"
KIND_DONE = "tt_done"
EXIT_TEXT = {"disaster": "disaster cap touched",
             "stop": "5m closed through the L2 stop",
             "tp1_be": "runner stopped at breakeven (entry touched)",
             "tp1_ratchet": "runner stopped at the ratcheted 5m low",
             "tp1_tp2": "runner off at TP2"}


def _size_line(risk):
    per_ct = 0.70 * risk * 100
    per_ct_atm = 0.55 * risk * 100
    n_itm = int(R_DOLLARS // per_ct) if per_ct > 0 else 0
    n_atm = int(R_DOLLARS // per_ct_atm) if per_ct_atm > 0 else 0
    if n_itm >= 1:
        return (f"**Size for ${R_DOLLARS:.0f} R: {n_itm} contract"
                f"{'s' if n_itm != 1 else ''} at 0.70Δ ITM · {n_atm} ATM (~0.55Δ).** "
                f"Round-down applied. _(One 0.70Δ contract ≈ ±${per_ct:.0f} at the stop.)_")
    return (f"**SKIP at ${R_DOLLARS:.0f} R** — one 0.70Δ contract risks ~${per_ct:.0f} "
            f"at the stop. Pass; never tighten the stop to fit.")


def run_tt_watch() -> str:
    """Every minute 9:47–15:59 ET."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    from zoneinfo import ZoneInfo
    et = ZoneInfo(ET)
    now = dt.datetime.now(et)
    today = now.date()
    if now.weekday() >= 5:
        return "weekend"
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, ticker, status, entry_trigger, stop, levels
                         FROM paper_specs WHERE book=%s AND trade_date=%s""",
                      (BOOK, today))
            spec = c.fetchone()
            if spec is None:
                return "no spec yet"
            sid, ticker, status, entry_db, stop_db, lv = spec
            if status in ("skipped_rank", "cancelled"):
                return status
            c.execute("SELECT kind, ref FROM discord_notify_log WHERE ref LIKE %s AND kind LIKE 'tt_%%'",
                      (f"{today.isoformat()}%",))
            sent = {(k, r) for k, r in c.fetchall()}
        lv = lv or {}
        client = get_client()
        if client is None:
            return "off"
        bars = _today_1m(client, ticker, today)
        if len(bars) < 6:
            return "warming"
        pdh, pmh = lv.get("pdh"), lv.get("pmh")
        if pdh is None and pmh is None and "holes" not in lv:
            pdh, pmh = level_inputs(conn, ticker, today)
        st = day_state(bars, pdh, pmh)
        fam, trig = st["families"], st["trigger"]
        # 🧱 confirmations, once per level
        for name in FAMILIES:
            f = fam[name]
            if f.get("confirm_ts") and (KIND_CONFIRM, f"{today.isoformat()}:{name}") not in sent:
                lvl = pdh if name == "pdh" else pmh
                msg = (f"🧱 **{label(ticker)} confirmed {name.upper()} {lvl:.2f}** on the "
                       f"{f['confirm_ts']:%H:%M} 5m close. Two-test live: watch the 1m for "
                       f"L1 (first retest low) → H (bounce high) → L2 (higher low at/above "
                       f"the level); the entry is the cross of H. A 5m close back through "
                       f"the level or a print under L1 kills it.")
                out = claim_and_send(KIND_CONFIRM, f"{today.isoformat()}:{name}", CHANNEL, msg, conn=conn)
                if out != "duplicate":
                    return out
        if trig is None:
            if now.time() > TRIGGER_END and (KIND_DONE, today.isoformat()) not in sent:
                msg = (f"✋ **{label(ticker)} two-test: no trigger by 15:00** — "
                       f"{verdict_text(fam)}. A structure that failed is a correct pass.")
                return claim_and_send(KIND_DONE, today.isoformat(), CHANNEL, msg, conn=conn)
            return "watching"
        # the trade: frozen levels from the book when it has written them,
        # else the same function on the same inputs
        j, entry, stop = trig["i_trig"], trig["entry"], trig["stop"]
        if status == "triggered" and entry_db and float(entry_db) > 0:
            entry, stop = float(entry_db), float(stop_db)
        if lv.get("tp1"):
            tp = {"tp1": lv.get("tp1"), "tp2": lv.get("tp2"), "levels": lv.get("tp_levels", {}),
                  "holes": lv.get("tp_holes", []), "fallback": lv.get("fallback", False)}
        else:
            tp = select_levels(bars, j, entry, pdh, pmh)
        tp1 = tp["tp1"]["px"] if tp.get("tp1") else None
        tp2 = tp["tp2"]["px"] if tp.get("tp2") else None
        risk = entry - stop
        if risk <= 0:
            return "hole"
        if (KIND_GO, today.isoformat()) not in sent:
            bar_ts = bars[j][0]
            age = (now - bar_ts).total_seconds() / 60
            late = " *(late alert — the trigger bar printed earlier)*" if age > 2.5 else ""
            tp1_r = f" (+{(tp1 - entry) / risk:.2f}R)" if tp1 else ""
            tp2_r = f" (+{(tp2 - entry) / risk:.2f}R)" if tp2 else ""
            runner = (f"**TP2 {tp2:.2f}**{tp2_r} ({tp['tp2']['kind']}): rest off on the touch."
                      if tp2 else "**No second level** — the runner rides the ratchet to the bell.")
            msg = (f"🪜 **TWO-TEST GO — {label(ticker)}** {trig['family'].upper()}: 1m "
                   f"{bar_ts:%H:%M} bar crossed H {trig['h']:.2f}.{late}\n"
                   f"**Entry {entry:.2f}**"
                   + (" (the bar opened above H — entry is the open)" if trig.get("gap_fill") else "")
                   + f" · stop **{stop:.2f}** (L2 {trig['l2']:.2f} − 0.05%; 5m CLOSE through = out) "
                   f"· disaster **{entry * 0.99:.2f}** (touch) — both until the first partial.\n"
                   + (f"**TP1 {tp1:.2f}**{tp1_r} ({tp['tp1']['kind']}): HALF off on the touch, "
                      f"then the runner's stop to **entry {entry:.2f}** on touch, ratcheted "
                      f"under each completed 5m low. {runner}\n" if tp1 else
                      f"_Levels: {describe_levels(tp)}._\n")
                   + f"{_size_line(risk)}\n_{PRIOR_TXT}_\n"
                   f"_Desk spec #{sid} — took it: journal_log; passed: journal_skip "
                   f"(spec_id={sid})._")
            return claim_and_send(KIND_GO, today.isoformat(), CHANNEL, msg, conn=conn)
        if (KIND_EXIT, today.isoformat()) in sent:
            return "done"
        state = lifecycle_state_v2(bars, j, entry, stop, tp1, tp2,
                                   final=now.time() >= dt.time(16, 0))
        if state["exit"] is not None:
            code, _ts, px = state["exit"]
            if code in ("eod_flat", "tp1_eod"):
                return "bell"
            r = (px - entry) / risk
            legs = " + ".join(f"{f * 100:.0f}% at {p:.2f} ({w})" for f, p, _t, w in state["legs"])
            msg = (f"🚪 **EXIT — {label(ticker)} two-test**: {EXIT_TEXT.get(code, code)}. Whole "
                   f"trade {r:+.2f}R from entry {entry:.2f} ({legs}). Close what is left now.")
            return claim_and_send(KIND_EXIT, today.isoformat(), CHANNEL, msg, conn=conn)
        if state["tp1_hit"] and (KIND_TP1, today.isoformat()) not in sent:
            nxt = (f"TP2 **{tp2:.2f}** ({tp['tp2']['kind']}): rest off on the touch."
                   if tp2 else "No second level — the runner rides to the bell.")
            msg = (f"💰 **{label(ticker)} two-test hit TP1 {tp1:.2f} "
                   f"({(tp1 - entry) / risk:+.2f}R) — HALF OFF.** Runner's stop to **entry "
                   f"{entry:.2f}** on touch; I'll ping each ratchet. {nxt}")
            return claim_and_send(KIND_TP1, today.isoformat(), CHANNEL, msg, conn=conn)
        if state["tp1_hit"] and state["stop"] is not None and state["stop"] > entry:
            ref = f"{today.isoformat()}:{state['stop']:.2f}"
            msg = (f"🔒 **{label(ticker)} two-test runner stop up to {state['stop']:.2f}** "
                   f"(completed 5m low; touch = out, {(state['stop'] - entry) / risk:+.2f}R "
                   f"locked on the half).")
            out = claim_and_send(KIND_RATCHET, ref, CHANNEL, msg, conn=conn)
            if out != "duplicate":
                return out
        if now.time() >= dt.time(15, 55) and (KIND_BELL, today.isoformat()) not in sent:
            msg = (f"🔔 **{label(ticker)} two-test still in at 3:55** — exit AT THE CLOSE; "
                   f"don't hold overnight.")
            return claim_and_send(KIND_BELL, today.isoformat(), CHANNEL, msg, conn=conn)
        return "holding"
    finally:
        conn.close()
