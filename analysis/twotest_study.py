"""
The TWO-TEST entry engine, graded (2026-09-12 — Eric, off his
"Intraday Level Trading" study workbook: 15m context → a COMPLETED 5m
close through a major level → on the 1m a first retest low L1, a
bounce high H, a second low L2 ABOVE L1 → enter on the cross of H;
"if we can find the edge in the stock selection and then focus on the
stock selection with the edge, this might actually help us read our
charts autonomously"). The workbook itself says it has no documented
win rate. This grades it on the stored 1m record, against the entry
families already graded (the plain 1m-gated retest is a coin flip
unconditioned; joined to leader days it pays), with the workbook's own
stop and filter beside the desk's.

SPEC (frozen before any number):
  universe   the 15 names with a 2-year RTH 1m record: mag7_1m_bars
             (AAPL MSFT NVDA AMZN GOOGL META TSLA) + liquid_1m_bars
             (SPY QQQ IWM AMD AVGO MU NFLX PLTR). Days with < 300 RTH
             1m bars are skipped (holes, recorded as such).
  levels     per day: PDH / PDL (daily_prices, prior session), PMH /
             PML (premarket_range), ORBH / ORBL (the first 30 minutes
             of the 1m record). Longs work the highs, shorts the lows
             (shorts RECORDED; the standing verdicts on short entries
             are stated at readout).
  confirm    tapeentry_study.level_machine's break leg, one definition:
             the first completed fixed-anchor 5m bar CLOSING beyond the
             level, after the tape has closed at-or-inside it (ORB
             starts inside by construction). 5m bars completing
             9:35–14:30 may confirm.
  structure  on the 1m bars strictly AFTER the confirming 5m bar:
             pivots are 1-bar fractals (a low below both neighbours,
             confirmed by the following bar). L1 = the first pivot low;
             H = the first pivot high after L1; L2 = the first pivot low
             after H whose low is ABOVE L1's low AND at-or-above the
             level (the structure must hold the new side). Mirror for
             shorts. The workbook's two clarifications hold: L2 need
             not revisit the level, and an extra retest after every
             small high is not demanded — the FIRST qualifying
             sequence is the setup.
  kill       before the trigger, a completed 5m close back through the
             level kills the day for that level (structure_failed:
             level_lost); a 1m print through L1's low kills it
             (structure_failed: l1_lost). No trigger by the cutoff =
             no_trigger. All three are RECORDED per (day, level) — a
             no-trigger failure is a correct pass and the workbook
             wants it counted.
  trigger    the first 1m bar after L2 is confirmed whose HIGH crosses
             H's high; entry = H's high (a stop-limit resting at the
             wick, the workbook's "a 1m close is not required"). Fills
             must complete by 15:00 ET. Outcomes are simulated from
             the NEXT 1m bar (the trigger bar's post-cross path cannot
             be ordered at 1m granularity — stated).
  stops      tapeentry_study.sim_stops, one definition, on the same
             entry: struct (L2's low − 0.05%, TOUCH — the workbook's
             tactical stop), struct_5c (the same level on a completed
             5m CLOSE — the desk's wick rule), pct25/50/100 touch,
             atr1, ema21x, plus eod (no stop). R on each variant's
             own unit; bps comparable across variants.
  room       first obstacle = the nearest of {PDH, PMH, ORBH, the
             session high so far} strictly above the entry for a long
             (mirror below for a short); NULL = no obstacle on the
             map (a hole, not infinite room). room_r = (obstacle −
             entry) / (entry − struct stop). Two exits on it:
             tp1 (exit at the obstacle on touch, struct touch stop,
             eod otherwise) and bracket2r (target 2× the struct risk,
             struct touch stop). The workbook's "~2R to the first
             obstacle" PASS filter is a readout CUT (room_r ≥ 2 vs
             < 2), never applied inside the grade.
  readout    by half (split 2025-09-01), by name, by family and side,
             leader day (rs_leader_events leader == this name) vs
             not, SPY day type, room ≥ 2 vs < 2 — beside the already
             graded plain retest and chase controls. BAR, frozen: the
             engine "helps" only if the struct_5c or eod variant is
             positive in BOTH halves AND sign-consistent in ≥ 8 of 15
             names; the 2R filter earns adoption only if it raises
             expectancy in BOTH halves. Entries at the wick cross, no
             costs, 1m granularity, survivor-free universe — stated
             wherever the numbers surface. The grade is the SIGNAL in
             underlying bps; options expression is the separate layer.

Writes ONLY twotest_events + twotest_days (zero-event days are rows);
resumes by twotest_days; marker twotest_v1.
"""
import datetime as dt
import json
import logging
import time

from analysis.tapeentry_study import (atr_series, ema_series, level_machine,
                                      resample5, sim_stops)

log = logging.getLogger("watchtower.twotest")

COMPLETE_MARKER = "twotest_v1"
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
LIQUID = ("SPY", "QQQ", "IWM", "AMD", "AVGO", "MU", "NFLX", "PLTR")
TICKERS = MAG7 + LIQUID
STOP_BUFF = 0.0005
ORB_MIN = 30
MIN_BARS = 300
CONFIRM_START = dt.time(9, 34)     # 5m bar whose last 1m starts 9:34 completes 9:35
CONFIRM_END = dt.time(14, 29)      # completes by 14:30
TRIGGER_END = dt.time(14, 59)      # completes by 15:00
BUDGET_S = 25 * 60
FAMILIES = (("pdh", "long"), ("pmh", "long"), ("orbh", "long"),
            ("pdl", "short"), ("pml", "short"), ("orbl", "short"))


def _bars_table(ticker):
    return "mag7_1m_bars" if ticker in MAG7 else "liquid_1m_bars"


# ── pure cores ───────────────────────────────────────────────────────

def pivots(bars, start, end):
    """Pure. 1-bar fractal pivots on bars[start:end] (index space of
    `bars`): [(i, 'low'|'high', px)] in order. A pivot at i is
    confirmed by bar i+1, so the last bar of the slice can never be a
    pivot."""
    out = []
    for i in range(max(start, 1), min(end, len(bars)) - 1):
        _, _, h, l, _ = bars[i]
        if l < bars[i - 1][3] and l < bars[i + 1][3]:
            out.append((i, "low", l))
        if h > bars[i - 1][2] and h > bars[i + 1][2]:
            out.append((i, "high", h))
    return out


def two_test(bars, level, direction, i_from, i_cutoff, bars5, last1m, i5_confirm):
    """Pure. The two-test structure after a confirmation, on the 1m bars
    bars[i_from:] (i_from = the first 1m bar AFTER the confirming 5m
    bar). Returns a dict with status in
    {'triggered', 'no_trigger', 'structure_failed'} and, when
    triggered, i_l1/l1, i_h/h, i_l2/l2, i_trig, entry.
    Kill rules: a completed 5m close back through the level, or a 1m
    print through L1's extreme, before the trigger. Longs need L2's low
    above L1's low and at-or-above the level; mirror for shorts."""
    sign = 1 if direction == "long" else -1
    pv = pivots(bars, i_from, i_cutoff + 1)
    # first-swing extreme kind for the retest leg
    first_kind, mid_kind = ("low", "high") if sign > 0 else ("high", "low")
    l1 = h = l2 = None
    for i, kind, px in pv:
        if l1 is None:
            if kind == first_kind:
                l1 = (i, px)
        elif h is None:
            if kind == mid_kind and i > l1[0]:
                h = (i, px)
        elif l2 is None:
            if kind == first_kind and i > h[0]:
                higher = (px > l1[1]) if sign > 0 else (px < l1[1])
                holds = (px >= level) if sign > 0 else (px <= level)
                if higher and holds:
                    l2 = (i, px)
                    break
    def _killed_before(j):
        # a completed 5m close back through the level, strictly after the
        # confirming 5m bar and completing at or before 1m index j
        for k in range(i5_confirm + 1, len(bars5)):
            if last1m[k] > j:
                break
            c5 = bars5[k][4]
            if (c5 < level) if sign > 0 else (c5 > level):
                return "level_lost"
        return None
    if l2 is None:
        # did the structure get killed before it could complete?
        end = min(i_cutoff, len(bars) - 1)
        why = _killed_before(end)
        if why is None and l1 is not None:
            for j in range(l1[0] + 1, end + 1):
                if (bars[j][3] < l1[1]) if sign > 0 else (bars[j][2] > l1[1]):
                    why = "l1_lost"
                    break
        return {"status": "structure_failed" if why else "no_trigger", "why": why,
                "l1": l1, "h": h, "l2": l2}
    # trigger: first bar after L2 is confirmed (i_l2 + 1 confirms it, so
    # scan from i_l2 + 2) whose extreme crosses H
    for j in range(l2[0] + 2, min(i_cutoff, len(bars) - 1) + 1):
        why = _killed_before(j)
        if why:
            return {"status": "structure_failed", "why": why, "l1": l1, "h": h, "l2": l2}
        if (bars[j][3] < l1[1]) if sign > 0 else (bars[j][2] > l1[1]):
            return {"status": "structure_failed", "why": "l1_lost", "l1": l1, "h": h, "l2": l2}
        crossed = (bars[j][2] > h[1]) if sign > 0 else (bars[j][3] < h[1])
        if crossed:
            return {"status": "triggered", "why": None, "l1": l1, "h": h, "l2": l2,
                    "i_trig": j, "entry": h[1]}
    return {"status": "no_trigger", "why": None, "l1": l1, "h": h, "l2": l2}


def first_obstacle(direction, entry, candidates):
    """Pure. Nearest candidate strictly beyond the entry in the trade's
    direction; None = no obstacle on the map (a hole)."""
    if direction == "long":
        above = [c for c in candidates if c is not None and c > entry]
        return min(above) if above else None
    below = [c for c in candidates if c is not None and c < entry]
    return max(below) if below else None


def sim_targets(bars1, i_after, entry, direction, stop_px, obstacle):
    """Pure. tp1 (obstacle on touch) and bracket2r (2× struct risk on
    touch), both with the struct TOUCH stop; same-bar stop-and-target
    is scored as a stop. R on the struct unit."""
    sign = 1.0 if direction == "long" else -1.0
    risk = sign * (entry - stop_px)
    out = {}
    for name, tgt in (("tp1", obstacle), ("bracket2r", entry + sign * 2 * risk if risk > 0 else None)):
        if tgt is None or risk <= 0:
            out[name] = None
            continue
        res = None
        for ts, o, h, l, c in bars1[i_after:]:
            hit_s = (l <= stop_px) if sign > 0 else (h >= stop_px)
            hit_t = (h >= tgt) if sign > 0 else (l <= tgt)
            if hit_s:
                res = {"out": "stopped", "exit_px": round(stop_px, 4)}
                break
            if hit_t:
                res = {"out": "target", "exit_px": round(tgt, 4)}
                break
        if res is None:
            res = {"out": "eod", "exit_px": round(bars1[-1][4], 4)}
        bps = sign * (res["exit_px"] / entry - 1) * 1e4
        res["bps"] = round(bps, 1)
        res["r"] = round(sign * (res["exit_px"] - entry) / risk, 2)
        out[name] = res
    return out


# ── the walk ─────────────────────────────────────────────────────────

def _grade_ticker(conn, ticker, done_days, deadline, et):
    table = _bars_table(ticker)
    with conn.cursor() as c:
        c.execute(f"SELECT ts, open, high, low, close FROM {table} WHERE ticker=%s ORDER BY ts",
                  (ticker,))
        raw = c.fetchall()
        c.execute("SELECT trade_date, high, low FROM daily_prices WHERE ticker=%s ORDER BY trade_date",
                  (ticker,))
        daily = c.fetchall()
        c.execute("SELECT trade_date, pm_high, pm_low FROM premarket_range WHERE ticker=%s", (ticker,))
        pm = {d: (float(h) if h is not None else None, float(l) if l is not None else None)
              for d, h, l in c.fetchall()}
    if len(raw) < 500:
        return 0
    bars_all = [(ts.astimezone(et), float(o), float(h), float(l), float(cl))
                for ts, o, h, l, cl in raw
                if dt.time(9, 30) <= ts.astimezone(et).time() <= dt.time(15, 59)]
    prev = {}
    for i in range(1, len(daily)):
        prev[daily[i][0]] = (float(daily[i - 1][1]), float(daily[i - 1][2]))
    by_day = {}
    for b in bars_all:
        by_day.setdefault(b[0].date(), []).append(b)
    # continuous 5m series for ATR / ema21 (matches an RTH chart)
    bars5_all, last1m_all = resample5(bars_all)
    e21_all = ema_series([b[4] for b in bars5_all], 21)
    atr_all = atr_series(bars5_all, 14)
    day5_idx = {}
    for k, b in enumerate(bars5_all):
        day5_idx.setdefault(b[0].date(), []).append(k)
    day1_off = {}
    off = 0
    for d in sorted(by_day):
        day1_off[d] = off
        off += len(by_day[d])
    n_written = 0
    for d in sorted(by_day):
        if d in done_days:
            continue
        if time.time() > deadline:
            return n_written
        bars = by_day[d]
        k5 = day5_idx.get(d, [])
        if len(bars) < MIN_BARS or d not in prev or not k5:
            with conn.cursor() as c:
                c.execute("INSERT INTO twotest_days (ticker, trade_date, n_bars, n_events, n_triggered) "
                          "VALUES (%s,%s,%s,NULL,NULL) ON CONFLICT DO NOTHING", (ticker, d, len(bars)))
            conn.commit()
            continue
        bars5 = [bars5_all[k] for k in k5]
        last1m = [last1m_all[k] - day1_off[d] for k in k5]
        e21_5 = [e21_all[k] for k in k5]
        atr5 = [atr_all[k] for k in k5]
        pdh, pdl = prev[d]
        pmh, pml = pm.get(d, (None, None))
        orb = [b for b in bars if b[0].time() < dt.time(9, 30 + ORB_MIN)]
        orbh = max(b[2] for b in orb) if orb else None
        orbl = min(b[3] for b in orb) if orb else None
        levels = {"pdh": pdh, "pdl": pdl, "pmh": pmh, "pml": pml, "orbh": orbh, "orbl": orbl}
        # 5m index windows for confirmation
        i5_start = next((k for k, b in enumerate(bars5) if b[0].time() >= CONFIRM_START), len(bars5))
        i5_end = next((k for k, b in enumerate(bars5) if b[0].time() > CONFIRM_END), len(bars5))
        i1_cut = next((i for i, b in enumerate(bars) if b[0].time() > TRIGGER_END), len(bars)) - 1
        rows = []
        n_trig = 0
        for fam, direction in FAMILIES:
            level = levels[fam]
            if level is None:
                rows.append((fam, direction, None, "no_level", None, None))
                continue
            start_inside = fam.startswith("orb")
            i5_from = max(i5_start, next((k for k, b in enumerate(bars5) if b[0].time() >= dt.time(9, 30 + ORB_MIN - 1)), len(bars5))) if start_inside else i5_start
            brk, _ = level_machine(bars5, level, direction, i5_from, i5_end, start_inside=start_inside)
            if brk is None:
                rows.append((fam, direction, level, "no_confirm", None, None))
                continue
            i_from = last1m[brk] + 1
            tt = two_test(bars, level, direction, i_from, i1_cut, bars5, last1m, brk)
            if tt["status"] != "triggered":
                rows.append((fam, direction, level, tt["status"], tt["why"], {
                    "confirm_ts": bars5[brk][0].isoformat(),
                    "l1": tt["l1"][1] if tt["l1"] else None, "h": tt["h"][1] if tt["h"] else None,
                    "l2": tt["l2"][1] if tt["l2"] else None}))
                continue
            n_trig += 1
            j = tt["i_trig"]
            entry = tt["entry"]
            sign = 1.0 if direction == "long" else -1.0
            struct_px = tt["l2"][1] * (1 - sign * STOP_BUFF)
            # 5m index of the trigger bar for the close-rule sims
            i5_after = next((k for k, li in enumerate(last1m) if li > j), len(bars5))
            eod_bps, mfe, mae, stops = sim_stops(bars, j + 1, entry, direction, struct_px,
                                                 atr5[min(i5_after, len(atr5) - 1)], bars5, i5_after, e21_5)
            hod = max(b[2] for b in bars[:j + 1])
            lod = min(b[3] for b in bars[:j + 1])
            cands = [pdh, pmh, orbh, hod] if direction == "long" else [pdl, pml, orbl, lod]
            obstacle = first_obstacle(direction, entry, cands)
            risk = sign * (entry - struct_px)
            room_r = round(sign * (obstacle - entry) / risk, 2) if (obstacle is not None and risk > 0) else None
            tgts = sim_targets(bars, j + 1, entry, direction, struct_px, obstacle)
            rows.append((fam, direction, level, "triggered", None, {
                "confirm_ts": bars5[brk][0].isoformat(),
                "l1": tt["l1"][1], "h": tt["h"][1], "l2": tt["l2"][1],
                "trigger_ts": bars[j][0], "entry": entry, "stop": struct_px,
                "obstacle": obstacle, "room_r": room_r,
                "eod_bps": eod_bps, "mfe_bps": mfe, "mae_bps": mae,
                "stops": stops, "tp1": tgts["tp1"], "bracket2r": tgts["bracket2r"]}))
        with conn.cursor() as c:
            for fam, direction, level, status, why, info in rows:
                info = info or {}
                c.execute("""INSERT INTO twotest_events
                    (trade_date, ticker, family, direction, level_px, status, why,
                     confirm_ts, l1_px, h_px, l2_px, trigger_ts, entry_px, stop_px,
                     obstacle_px, room_r, eod_bps, mfe_bps, mae_bps, stops, tp1, bracket2r)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (d, ticker, fam, direction, level, status, why,
                     info.get("confirm_ts"), info.get("l1"), info.get("h"), info.get("l2"),
                     info.get("trigger_ts"), info.get("entry"), info.get("stop"),
                     info.get("obstacle"), info.get("room_r"),
                     info.get("eod_bps"), info.get("mfe_bps"), info.get("mae_bps"),
                     json.dumps(info["stops"]) if info.get("stops") else None,
                     json.dumps(info["tp1"]) if info.get("tp1") else None,
                     json.dumps(info["bracket2r"]) if info.get("bracket2r") else None))
            c.execute("INSERT INTO twotest_days (ticker, trade_date, n_bars, n_events, n_triggered) "
                      "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      (ticker, d, len(bars), len(rows), n_trig))
        conn.commit()
        n_written += 1
    return n_written


def run() -> bool:
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    conn = _conn()
    t0 = time.time()
    deadline = t0 + BUDGET_S
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (COMPLETE_MARKER,))
            if c.fetchone():
                return True
        for tk in TICKERS:
            if time.time() > deadline:
                log.info("[twotest] budget hit; resuming next pass.")
                return False
            with conn.cursor() as c:
                c.execute("SELECT trade_date FROM twotest_days WHERE ticker=%s", (tk,))
                done = {r[0] for r in c.fetchall()}
            try:
                n = _grade_ticker(conn, tk, done, deadline, et)
            except Exception as e:
                conn.rollback()
                log.warning("[twotest] %s failed: %s", tk, str(e)[:300])
                return False
            log.info("[twotest] %s: +%d day(s)", tk, n)
        # complete only when every stored bar day is graded
        with conn.cursor() as c:
            c.execute("""SELECT count(*) FROM (
                           SELECT DISTINCT ticker, trade_date FROM mag7_1m_bars
                           UNION SELECT DISTINCT ticker, trade_date FROM liquid_1m_bars) b
                         WHERE NOT EXISTS (SELECT 1 FROM twotest_days d
                                           WHERE d.ticker=b.ticker AND d.trade_date=b.trade_date)""")
            todo = c.fetchone()[0]
        if todo == 0:
            with conn.cursor() as c:
                c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                          "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
            conn.commit()
            log.info("[twotest] complete — marker %s.", COMPLETE_MARKER)
            return True
        return False
    finally:
        conn.close()


READOUT_SQL = """
-- the engine by half × side, every variant on one scale (NULL-guarded caps)
SELECT CASE WHEN trade_date < '2025-09-01' THEN 'h1' ELSE 'h2' END half, direction, count(*) n,
       round(avg(eod_bps),1) eod_bps, round(100.0*count(*) FILTER (WHERE eod_bps > 0)/count(*),0) pct_pos,
       round(avg((stops->'struct'->>'r')::numeric),2) struct_r,
       round(avg((stops->'struct_5c'->>'r')::numeric),2) struct_5c_r,
       round(avg(LEAST(GREATEST((tp1->>'r')::numeric,-10),10)) FILTER (WHERE tp1 IS NOT NULL),2) tp1_r,
       round(avg((bracket2r->>'r')::numeric),2) bracket2r_r,
       count(*) FILTER (WHERE room_r >= 2) room_ok
FROM twotest_events WHERE status='triggered' GROUP BY 1,2 ORDER BY 1,2;
"""
