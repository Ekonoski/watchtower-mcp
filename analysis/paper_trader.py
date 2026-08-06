"""
Watchtower — paper-trading platform v1 (the measurement engine).

Two books, never blended (see paper_specs.book):
  gamma  — the playbook's qualified day-trade plays, generated each morning
           from gex_levels. The unproven edge this platform exists to measure.
  swing  — neckline-family breakout-retest limits from pattern_scan. Already
           backtested; runs as the control group (and as the blind-limit vs
           stall-entry experiment).

House rules encoded here, not approximated:
  - Wick rule: every trigger and stop is a COMPLETED 15-minute-bar close,
    never a tick. A wick through a level changes nothing.
  - Magnitude rule: gamma specs only when |net GEX| >= 1.0bn (load-bearing).
  - Matrix gates: no directional specs on a collapsed magnet (CW == PW);
    no put-wall dip-buys in v1 (the flip gate makes them rare and they are
    the easiest rule to get subtly wrong — excluded until the ledger earns
    the complexity).
  - Geometry filter: target room must be >= 1.5x stop distance or no spec.
  - Binary gate: NFP / CPI / FOMC / PCE days mark every gamma spec
    skipped_binary at spec time. Other 10:00 ET High-impact releases block
    NEW entries 9:55-10:15 (loop-level).
  - Clock rules: no new entries after 14:30 ET; everything force-flat at
    15:55 (exit_reason eod_flat); two stops in one book in one day halts
    that book (specs -> cancelled).
  - R is computed against the SPEC's stop distance; fills are the trigger
    bar's close. Slippage lives in the gap between spec R and realized R.
"""
import datetime as dt
import logging
import os
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screen.reversal_screen import _conn as get_db_connection  # noqa: E402
from analysis.polygon_data import fetch_recent_bars  # noqa: E402

log = logging.getLogger("watchtower.paper")
ET = zoneinfo.ZoneInfo("America/New_York")
VENUE = ["SPY", "QQQ", "IWM"]
BINARY_EVENTS = ("Non Farm Payrolls", "CPI", "FOMC", "Interest Rate Decision",
                 "Core PCE")
SWING_PATTERNS = ("double_bottom", "inverse_hs", "higher_low")


def _touch(level, px):
    return abs(px - level) / level <= 0.001


def build_gamma_specs(trade_date, levels, status="armed"):
    """Playbook rules → paper_specs rows. The single source of truth: the live
    morning spec-writer and the replay harness (analysis/gamma_replay.py) both
    call this, so a rule change backtests and trades identically.

    levels: iterable of (ticker, spot, call_wall, put_wall, gamma_flip,
            net_gex, regime) — the freshest board per ticker.
    Returns (specs, skips): specs are paper_specs value tuples,
    skips are (ticker, reason) so "no spec" is always explainable.
    """
    specs, skips = [], []
    for tk, spot, cw, pw, flip, gex, regime in levels:
        spot, cw, pw = float(spot), float(cw or 0), float(pw or 0)
        flip = float(flip) if flip is not None else None
        gex = float(gex or 0)
        why_skip = None
        if abs(gex) < 1.0:
            why_skip = f"net GEX {gex:+.2f}bn below load-bearing"
        elif cw and pw and cw == pw:
            why_skip = f"collapsed magnet at {cw} — matrix row three"
        if why_skip:
            skips.append((tk, why_skip))
            continue
        # Wall fade (pinning, spot below CW): short the stall at the wall.
        if regime == "pinning" and cw and spot < cw:
            tgt = max(flip or 0, (cw + pw) / 2 if pw else 0)
            stop = round(cw * 1.0015, 2)
            if tgt and (cw - tgt) >= 1.5 * (stop - cw):
                specs.append((trade_date, "gamma", tk, "short", f"wall_fade_{cw:g}",
                              cw, stop, round(tgt, 2), status,
                              f"first-touch fade at {cw:g} CW, {gex:+.1f}bn pinning; "
                              f"entry=15m close back under wall after touch; "
                              f"stop=15m close beyond {stop}; target {tgt:g}"))
        # Flip-hold long (pinning, flip below spot, room to CW).
        if regime == "pinning" and flip and cw and flip < spot < cw:
            stop = round(flip * 0.9985, 2)
            if (cw - flip) >= 1.5 * (flip - stop):
                specs.append((trade_date, "gamma", tk, "long", f"flip_hold_{flip:g}",
                              flip, stop, cw, status,
                              f"flip-hold long at {flip:g} ({gex:+.1f}bn pinning); "
                              f"entry=touch then 15m close back above flip; "
                              f"stop=15m close under {stop}; target CW {cw:g}"))
        # Slippery stack fade: CW and flip within 0.5% = the stack.
        if regime == "slippery" and flip and cw and spot < min(flip, cw) \
                and abs(cw - flip) / flip <= 0.005:
            stack = max(cw, flip)
            stop = round(stack * 1.0015, 2)
            tgt = round(spot - (stack - spot), 2)  # symmetric room, capped by geometry
            if (stack - tgt) >= 1.5 * (stop - stack):
                specs.append((trade_date, "gamma", tk, "short", f"stack_fade_{stack:g}",
                              stack, stop, tgt, status,
                              f"slippery stack fade {stack:g} (CW+flip, {gex:+.1f}bn); "
                              f"counter-trend entry, with-trend hold"))
    return specs, skips


# ── Morning spec-writer (7:40 ET, after the 7:30 gamma sweep) ────────────────

def write_morning_specs():
    today = dt.datetime.now(ET).date()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM paper_specs WHERE trade_date=%s LIMIT 1", (today,))
            if c.fetchone():
                log.info("[paper] specs already written for %s", today)
                return
            c.execute("""SELECT event FROM economic_calendar
                         WHERE country='US' AND event_date=%s AND impact='High'""", (today,))
            highs = [r[0] for r in c.fetchall()]
        binary_day = any(b.lower() in e.lower() for e in highs for b in BINARY_EVENTS)
        status = "skipped_binary" if binary_day else "armed"

        with conn.cursor() as c:
            c.execute("""SELECT DISTINCT ON (ticker) ticker, spot, call_wall, put_wall,
                                gamma_flip, net_gex, regime
                         FROM gex_levels WHERE ticker = ANY(%s)
                         ORDER BY ticker, computed_at DESC""", (VENUE,))
            specs, skips = build_gamma_specs(today, c.fetchall(), status)
            for tk, why_skip in skips:
                log.info("[paper] %s: no gamma spec — %s", tk, why_skip)

            # Swing book: breakout-retest limits (blind by design — control group).
            c.execute("""SELECT ticker, timeframe, pattern, direction, trigger_price,
                                target, invalid_level, score
                         FROM pattern_scan
                         WHERE pattern = ANY(%s) AND status='breakout' AND score >= 70
                           AND direction='bullish'
                           AND dist_to_trigger_pct BETWEEN 0 AND 4""", (list(SWING_PATTERNS),))
            for tk, tf, pat, _dir, trig, tgt, inv, score in c.fetchall():
                trig, tgt, inv = float(trig), float(tgt), float(inv)
                if (tgt - trig) < 1.5 * (trig - inv):
                    continue
                specs.append((today, "swing", tk, "long", f"retest_{pat}_{tf}",
                              trig, inv, tgt, "armed",
                              f"{pat} {tf} breakout (score {score}); blind limit at the "
                              f"trigger per retest doctrine; stop=pattern invalid {inv:g}"))

        with conn.cursor() as c:
            c.executemany("""INSERT INTO paper_specs
                (trade_date, book, ticker, direction, setup, entry_trigger, stop,
                 target, status, rationale)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", specs)
        conn.commit()
        log.info("[paper] %d specs written for %s (binary_day=%s: %s)",
                 len(specs), today, binary_day, ", ".join(highs) or "none")
    finally:
        conn.close()


# ── Intraday trigger loop (every 5 min, 9:35–15:55 ET) ───────────────────────

def _last_closed_15m(tk):
    """Completed 15m bars today, oldest→newest: [(ts_et, close, high, low)]."""
    bars = fetch_recent_bars(tk, days=2, multiplier=15, timespan="minute")
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for b in bars:
        ts = b.get("timestamp")
        t = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc) if ts else None
        if t and t + dt.timedelta(minutes=15) <= now:   # completed only
            te = t.astimezone(ET)
            if te.date() == dt.datetime.now(ET).date():
                out.append((te, float(b["close"]), float(b["high"]), float(b["low"])))
    return out


def run_trigger_loop():
    now = dt.datetime.now(ET)
    if now.weekday() >= 5 or not (dt.time(9, 35) <= now.time() <= dt.time(15, 58)):
        return
    today = now.date()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT s.id, s.book, s.ticker, s.direction, s.setup,
                                s.entry_trigger, s.stop, s.target, s.status,
                                t.id, t.entry_px, t.entered_at, t.exited_at
                         FROM paper_specs s LEFT JOIN paper_trades t ON t.spec_id=s.id
                         WHERE s.trade_date=%s AND s.status IN ('armed','triggered')""",
                      (today,))
            rows = c.fetchall()
        if not rows:
            return
        # two-stop halt, per book
        with conn.cursor() as c:
            c.execute("""SELECT s.book, count(*) FROM paper_trades t
                         JOIN paper_specs s ON s.id=t.spec_id
                         WHERE s.trade_date=%s AND t.exit_reason='stop'
                         GROUP BY s.book""", (today,))
            halted = {b for b, n in c.fetchall() if n >= 2}
        eod = now.time() >= dt.time(15, 55)
        no_new = eod or now.time() >= dt.time(14, 30)

        for (sid, book, tk, direction, setup, trig, stop, tgt, status,
             tid, entry_px, entered_at, exited) in rows:
            trig, stop, tgt = float(trig), float(stop), float(tgt)
            if exited:
                continue
            bars = _last_closed_15m(tk)
            if not bars:
                continue
            ts, close, hi, lo = bars[-1]
            sign = 1 if direction == "long" else -1
            if tid is None:                          # not entered yet
                if book in halted or no_new:
                    if eod:
                        _cancel(conn, sid, "day over")
                    continue
                touched = any((lo <= trig <= hi) or _touch(trig, c2)
                              for _, c2, hi, lo in bars)
                if book == "swing":
                    entered = touched                # blind limit — by design
                else:
                    entered = touched and (sign * (close - trig) > 0)  # 15m close back through
                if entered:
                    with conn.cursor() as c:
                        c.execute("""INSERT INTO paper_trades (spec_id, entered_at, entry_px)
                                     VALUES (%s, now(), %s)""", (sid, trig if book == "swing" else close))
                        c.execute("UPDATE paper_specs SET status='triggered' WHERE id=%s", (sid,))
                    conn.commit()
                    log.info("[paper] ENTER %s %s %s @ %.2f (%s)", book, tk, direction, close, setup)
            else:                                    # open — manage exit
                entry_px = float(entry_px)
                r_dist = abs(trig - stop) or 0.01
                # Entry fills at the entry bar's close, so that bar's range is
                # pre-entry price action — grading its high/low as a fill would
                # be lookahead. Stop/target only count on bars ending after entry.
                post_entry = entered_at is None or ts + dt.timedelta(minutes=15) > entered_at
                exit_px, reason = None, None
                if post_entry and sign * (close - stop) < 0:  # 15m close beyond stop = acceptance
                    exit_px, reason = close, "stop"
                elif post_entry and ((direction == "long" and hi >= tgt)
                                     or (direction == "short" and lo <= tgt)):
                    exit_px, reason = tgt, "target"
                elif eod:
                    exit_px, reason = close, "eod_flat"
                if exit_px is not None:
                    r_mult = round(sign * (exit_px - entry_px) / r_dist, 2)
                    with conn.cursor() as c:
                        c.execute("""UPDATE paper_trades SET exited_at=now(), exit_px=%s,
                                     exit_reason=%s, r_multiple=%s WHERE id=%s""",
                                  (exit_px, reason, r_mult, tid))
                    conn.commit()
                    log.info("[paper] EXIT %s %s %s @ %.2f (%s, %+.2fR)",
                             book, tk, direction, exit_px, reason, r_mult)
    finally:
        conn.close()


def _cancel(conn, sid, note):
    with conn.cursor() as c:
        c.execute("UPDATE paper_specs SET status='cancelled', "
                  "rationale = rationale || ' | cancelled: ' || %s WHERE id=%s", (note, sid))
    conn.commit()
