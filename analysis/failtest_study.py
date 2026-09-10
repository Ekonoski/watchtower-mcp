"""
The range-edge FAILURE TEST (2026-09-10 — Eric: "Run the failure test
study now"; pre-registered in docs/research/level_trading_practice.md
§4). The auction-theory trade for a range day: price probes beyond a
range edge, fails to find participation, closes back inside; enter
against the break, target the far side. The desk has never graded it;
its nearest cousins (the blind and confirmed PDL fades, the tapebot
flip) all graded as chop. This grades it on the stored index record.

SPEC (frozen before any number):
  universe   SPY (2005→) and QQQ (2011→), index_intraday_bars 15m RTH
             (bars starting 9:30..15:45) + daily_prices for yesterday's
             high/low. A day needs ≥ 24 bars.
  levels     four edges per day: PDH / PDL (yesterday's high/low) and
             ORBH / ORBL (the high/low of the first two 15m bars,
             9:30–10:00). PD probes may start at the 9:30 bar; ORB
             probes start at 10:00 (the range must exist). Probe time
             is stored so early (< 10:30) vs late cuts at readout.
  event      the FIRST bar of the day to CLOSE beyond an edge (the
             probe), whose NEXT bar CLOSES back inside — one event per
             edge per day. A second consecutive close beyond = the
             level was accepted, no event for that edge that day. A
             wick beyond without a close is not a probe (wick rule).
  entry      at the failing bar's close, AGAINST the break: short a
             failed high-side probe, long a failed low-side probe.
  stop       the probe bar's extreme. Two exits recorded on the same
             entry: `close` — exit on a 15m CLOSE beyond it (the wick
             rule the index books use); `touch` — exit on a touch.
  target     the opposite edge of the SAME range (PDL for a PDH
             failure, ORBL for an ORBH failure, and mirrors), filled on
             a touch (a resting limit). Same-bar touch-and-stop = stop
             (conservative; 15m bars cannot order intrabar events).
  eod        no target/stop → exit at the last bar's close (15:45 bar,
             the 15m record's close).
  outcomes   per exit variant: outcome (target/stop/eod), r on the
             entry-to-stop unit, bps in the trade's direction, mfe/mae
             bps, bars held; reward/risk of the setup as declared.
  readout    by ticker × era (pre/post 2016) × family × side, cut by
             the SPY/QQQ 9:45 day-type verdict (RANGE / UNDECIDED /
             TRAVEL, joined from daytype_days), probe time, and
             open_state. BAR, frozen: positive in both eras AND on
             both tickers; cells under 40 render small-n. Entries at
             closes, no costs, 15m granularity — stated wherever the
             numbers surface. If it grades it becomes a declared entry
             for RANGE LIKELY days with its own arming rule; if not,
             range days stay stand-aside for premium.

Writes ONLY failtest_events and failtest_days (a row per graded day so
zero-event days are recorded, not missing). Marker failtest_v1.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.failtest")

COMPLETE_MARKER = "failtest_v1"
TICKERS = ("SPY", "QQQ")
MIN_BARS = 24
ORB_BARS = 2
BUDGET_S = 15 * 60
FAMILIES = (("pdh", "high"), ("pdl", "low"), ("orbh", "high"), ("orbl", "low"))


# ── pure cores ───────────────────────────────────────────────────────

def edges(bars, pdh, pdl):
    """Pure. The day's four edges and the index from which probes of
    each may start."""
    orb_h = max(b[2] for b in bars[:ORB_BARS])
    orb_l = min(b[3] for b in bars[:ORB_BARS])
    return {"pdh": (pdh, pdl, 0), "pdl": (pdl, pdh, 0),
            "orbh": (orb_h, orb_l, ORB_BARS), "orbl": (orb_l, orb_h, ORB_BARS)}


def find_events(bars, pdh, pdl):
    """Pure. bars = the day's 15m RTH bars [(ts, o, h, l, c)] in order.
    Returns one dict per edge that printed a failure test: family,
    side, level, target, i_probe, i_entry, entry, stop, direction."""
    out = []
    if len(bars) < ORB_BARS + 2 or pdh is None or pdl is None:
        return out
    for fam, side in FAMILIES:
        level, target, start = edges(bars, pdh, pdl)[fam]
        if level is None or target is None:
            continue
        for i in range(start, len(bars) - 1):
            c = bars[i][4]
            beyond = c > level if side == "high" else c < level
            if not beyond:
                continue
            nxt = bars[i + 1][4]
            inside = nxt < level if side == "high" else nxt > level
            if inside:
                out.append({
                    "family": fam, "side": side, "level": level, "target": target,
                    "i_probe": i, "i_entry": i + 1, "entry": nxt,
                    "stop": bars[i][2] if side == "high" else bars[i][3],
                    "direction": "short" if side == "high" else "long",
                })
            break                      # first close beyond decides the edge's day
    return out


def simulate(bars, ev, stop_rule="close"):
    """Pure. From the bar AFTER entry to the day's last bar: target on
    touch, stop on a 15m close beyond (stop_rule='close') or a touch
    ('touch'); same-bar target-and-stop = stop. Returns outcome, r,
    bps, mfe/mae bps, bars_held, exit_px."""
    entry, stop, target = ev["entry"], ev["stop"], ev["target"]
    sign = 1.0 if ev["direction"] == "long" else -1.0
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    mfe = mae = 0.0
    outcome, exit_px, held = "eod", bars[-1][4], 0
    for j in range(ev["i_entry"] + 1, len(bars)):
        ts, o, h, l, c = bars[j]
        held += 1
        fav = sign * ((h if sign > 0 else l) - entry)
        adv = sign * ((l if sign > 0 else h) - entry)
        mfe, mae = max(mfe, fav), min(mae, adv)
        hit_t = (h >= target) if sign > 0 else (l <= target)
        if stop_rule == "touch":
            hit_s = (l <= stop) if sign > 0 else (h >= stop)
            s_px = stop
        else:
            hit_s = (c < stop) if sign > 0 else (c > stop)
            s_px = c
        if hit_s:
            outcome, exit_px = "stop", s_px
            break
        if hit_t:
            outcome, exit_px = "target", target
            break
    r = sign * (exit_px - entry) / risk
    return {"outcome": outcome, "r": round(r, 3),
            "bps": round(sign * (exit_px / entry - 1) * 1e4, 1),
            "mfe_bps": round(mfe / entry * 1e4, 1), "mae_bps": round(mae / entry * 1e4, 1),
            "bars_held": held, "exit_px": round(exit_px, 4)}


# ── the seeder ───────────────────────────────────────────────────────

def run() -> bool:
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (COMPLETE_MARKER,))
            if c.fetchone():
                return True
        all_done = True
        for tk in TICKERS:
            with conn.cursor() as c:
                c.execute("SELECT max(trade_date) FROM failtest_days WHERE ticker=%s", (tk,))
                r = c.fetchone()
                last_done = r[0] if r and r[0] else None
                c.execute("""SELECT trade_date, high, low FROM daily_prices
                             WHERE ticker=%s AND high IS NOT NULL AND low IS NOT NULL
                             ORDER BY trade_date""", (tk,))
                daily = c.fetchall()
                c.execute("""SELECT trade_date, ts, open, high, low, close FROM index_intraday_bars
                             WHERE ticker=%s AND (%s IS NULL OR trade_date > %s) ORDER BY ts""",
                          (tk, last_done, last_done))
                rows = c.fetchall()
            prev = {}
            for i in range(1, len(daily)):
                prev[daily[i][0]] = (float(daily[i - 1][1]), float(daily[i - 1][2]))
            by_day = {}
            for d, ts, o, h, l, cl in rows:
                t = ts.astimezone(et)
                if dt.time(9, 30) <= t.time() < dt.time(16, 0):
                    by_day.setdefault(d, []).append((t, float(o), float(h), float(l), float(cl)))
            for d in sorted(by_day):
                if time.time() - t0 > BUDGET_S:
                    log.info("[failtest] budget hit; resuming next pass.")
                    return False
                bars = by_day[d]
                if len(bars) < MIN_BARS or d not in prev:
                    with conn.cursor() as c:
                        c.execute("INSERT INTO failtest_days (ticker, trade_date, n_events, n_bars) "
                                  "VALUES (%s,%s,NULL,%s) ON CONFLICT DO NOTHING", (tk, d, len(bars)))
                    conn.commit()
                    continue                              # a hole, recorded as one
                pdh, pdl = prev[d]
                evs = find_events(bars, pdh, pdl)
                with conn.cursor() as c:
                    for ev in evs:
                        sc = simulate(bars, ev, "close")
                        st = simulate(bars, ev, "touch")
                        if sc is None or st is None:
                            continue
                        reward = abs(ev["target"] - ev["entry"])
                        risk = abs(ev["entry"] - ev["stop"])
                        c.execute("""INSERT INTO failtest_events
                            (ticker, trade_date, family, side, direction, level_px, target_px,
                             probe_ts, entry_ts, entry_px, stop_px, rr_declared,
                             outcome_close, r_close, bps_close, bars_close,
                             outcome_touch, r_touch, bps_touch, mfe_bps, mae_bps)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT DO NOTHING""",
                            (tk, d, ev["family"], ev["side"], ev["direction"],
                             round(ev["level"], 4), round(ev["target"], 4),
                             bars[ev["i_probe"]][0], bars[ev["i_entry"]][0],
                             round(ev["entry"], 4), round(ev["stop"], 4),
                             round(reward / risk, 3) if risk > 0 else None,
                             sc["outcome"], sc["r"], sc["bps"], sc["bars_held"],
                             st["outcome"], st["r"], st["bps"], sc["mfe_bps"], sc["mae_bps"]))
                    c.execute("INSERT INTO failtest_days (ticker, trade_date, n_events, n_bars) "
                              "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (tk, d, len(evs), len(bars)))
                conn.commit()
            log.info("[failtest] %s: graded through %s", tk, max(by_day) if by_day else last_done)
        if all_done:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                          "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
            conn.commit()
            log.info("[failtest] complete — marker %s.", COMPLETE_MARKER)
        return all_done
    finally:
        conn.close()


READOUT_SQL = """
-- ticker × era × family × side, both exit rules (NULL-guarded caps)
SELECT e.ticker, CASE WHEN e.trade_date < '2016-01-01' THEN 'pre2016' ELSE 'post2016' END era,
       e.family, e.direction, count(*) n,
       round(100.0*avg((e.bps_close>0)::int),1) win_close, round(avg(e.bps_close),1) bps_close,
       round(avg(LEAST(GREATEST(e.r_close,-10),10)),3) r_close,
       round(100.0*avg((e.bps_touch>0)::int),1) win_touch, round(avg(LEAST(GREATEST(e.r_touch,-10),10)),3) r_touch,
       round(avg(e.rr_declared),2) rr
FROM failtest_events e GROUP BY 1,2,3,4 ORDER BY 1,2,3,4;
"""
