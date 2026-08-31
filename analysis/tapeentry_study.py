"""
The tape-entry study (2026-09-02, pre-registered before any number —
Eric: "find the most liquid names for options and test them to tell me
which entries are the best regarding our indicator watchtower bot. I'm
assuming, and it is an assumption, that a retest of some level is the
best probability. I then want you to tell me via the study where the
best stop is to be set. I will be mechanical with this.").

UNIVERSE (11, frozen): SPY QQQ IWM AAPL MSFT NVDA AMZN GOOGL META TSLA
AMD — his scanner 7 plus the rest of the mag-7. Every name is either
verified liquid in our own iv_history OI record (NVDA/AMZN/MSFT/GOOG)
or a perennial top-10 options venue (external fact, stated). Bars:
mag7_1m_bars + liquid_1m_bars, 2 years of RTH 1m.

DEFINITIONS — the Scanner's own, ported (one definition, never
reimplemented by feel). 5m bars are FIXED-ANCHOR resamples of the 1m
record from 9:30 (no repaint); EMAs/trend/ATR run CONTINUOUSLY across
days per ticker, matching an RTH chart. Trend = the Scanner v1.5 gate
(choppy = >=4 crosses of close vs ema21 in the trailing 14 pairs; bull
= ema8>ema21; expanding spread; close beyond ema21). Wick rule
everywhere: a touch qualifies only if the bar CLOSES holding. Days
with fewer than 300 RTH 1m bars (half days, vendor holes) are skipped.

ENTRY FAMILIES (first qualifying per day/name/family/direction;
decided on bars COMPLETING 9:35-15:00 ET; longs need trend=+1, shorts
trend=-1 — shorts are RECORDED for the record; the standing verdicts
on short entries are stated at readout):
  ema8_5m       5m trend on; 5m bar touches the 5m 8 EMA and closes
                holding -> enter at that 5m close.
  ema21_5m      same at the 21 EMA.
  ema_1m_gated  the Scanner v1.7 column: last COMPLETED 5m bar's trend
                on, 1m's own trend on, 1m bar touches the 1m 8 or 21
                EMA and closes holding -> enter at the 1m close.
  orb_rt        30-min opening range; first post-ORB 5m CLOSE beyond
                the ORB edge = break; then the first 5m bar that comes
                back to touch the edge and CLOSES beyond it -> enter.
                A 5m close back through the edge before the retest
                kills the setup for the day.
  pdh_rt        the same machine at PDH (shorts: PDL), requiring a 5m
                close at-or-inside the level first (a day that opens
                beyond and never looks back has no break to buy).
  orb_chase     CONTROL: enter at the break close itself, no retest.
  pdh_chase     CONTROL: same at PDH/PDL.

STOP GRID (each simulated on the same entries from the 1m record;
touch stops exit at the stop price, close-rule stops exit at the
triggering completed 5m CLOSE — the wick-rule variant priced
explicitly):
  struct     pullback-bar extreme -0.05% buffer, exit on touch
  struct_5c  same level, exit only on a completed 5m CLOSE beyond
  pct25 / pct50 / pct100   0.25% / 0.50% / 1.00% from entry, touch
  atr1       1.0 x ATR14(5m) at entry, touch
  ema21x     exit on a 5m CLOSE beyond the 5m 21 EMA (no fixed risk
             unit -> graded in bps only, r is NULL, stated)
Unstopped positions exit at the true close. No profit targets in v1 —
the entry question is graded to EOD; bracket variants are a follow-up.

OUTCOMES per entry: eod_bps (no stop), mfe/mae bps, and per stop
variant {out, exit_px, bps, r}. Stop comparison reads BPS expectancy
(comparable across stops) beside R (per-stop risk units differ — a
tight stop inflates R; both render, stated), plus %-stopped and the
whipsaw rate (stopped on a day that closed favorable).

BAR (pre-registered): an entry family is "best" only if positive in
BOTH year-halves AND sign-consistent in >=7 of 11 names. A stop
verdict must hold on the winning family in both halves. Caveats
wherever numbers surface: entries at bar closes, no costs/slippage,
1m granularity, survivor-free universe (all 11 trade today), and this
grades the SIGNAL in underlying bps — options expression is the
separate layer per the ledger-grades-the-signal rule.

Writes ONLY tapeentry_events + tapeentry_days (zero-entry days are
recorded rows — the _social_block rule); resumes by tapeentry_days;
marker tapeentry_study_v1 once both bars markers exist and all stored
days are graded.
"""
import datetime as dt
import json
import logging
import time

log = logging.getLogger("watchtower.tapeentry")

COMPLETE_MARKER = "tapeentry_study_v1"
BARS_MARKERS = ("rsleader_bars_v1", "liquid_bars_v1")
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
LIQUID = ("SPY", "QQQ", "IWM", "AMD")
STOP_BUFF = 0.0005
# bar timestamps are the bar's LAST 1m start; a bar with ts T completes
# at T+1m, so start 9:34 / cutoff 14:59 = "completing 9:35-15:00".
W_START = dt.time(9, 34)
W_END = dt.time(14, 59)
ORB_MIN = 30
BUDGET_S = 20 * 60


# ── pure cores ───────────────────────────────────────────────────────

def ema_series(vals, n):
    out, k = [], 2 / (n + 1)
    e = vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def atr_series(bars, n=14):
    """Wilder RMA of true range. bars = [(ts, o, h, l, c), ...]."""
    out = []
    a = None
    prev_c = None
    for ts, o, h, l, c in bars:
        tr = h - l if prev_c is None else max(h - l, abs(h - prev_c),
                                              abs(l - prev_c))
        a = tr if a is None else (a * (n - 1) + tr) / n
        out.append(a)
        prev_c = c
    return out


def resample5(bars):
    """Fixed-anchor 5m resample of RTH 1m bars: buckets by
    (minutes since 9:30) // 5 within each trade date — never
    end-anchored (the BW-3D repaint lesson). Returns
    (bars5, last1m) where bars5[k] = (ts_of_last_1m, o, h, l, c) and
    last1m[k] = index of that 5m bar's final 1m bar."""
    out, last1m = [], []
    cur_key = None
    o = h = l = c = None
    for i, (ts, bo, bh, bl, bc) in enumerate(bars):
        mins = (ts.hour - 9) * 60 + ts.minute - 30
        key = (ts.date(), mins // 5)
        if key != cur_key:
            if cur_key is not None:
                out.append((bars[last1m[-1]][0], o, h, l, c))
            cur_key, o, h, l, c = key, bo, bh, bl, bc
            last1m.append(i)
        else:
            h = max(h, bh)
            l = min(l, bl)
            c = bc
            last1m[-1] = i
    if cur_key is not None:
        out.append((bars[last1m[-1]][0], o, h, l, c))
    return out, last1m


def trend_series(closes, e8, e21):
    """The Scanner v1.5 trend gate, per bar: -1 / 0 / +1."""
    n = len(closes)
    spread = [abs(e8[i] - e21[i]) for i in range(n)]
    out = [0] * n
    for i in range(n):
        if i < 21:
            continue
        cnt = 0
        for k in range(1, 15):
            if (closes[i - k] > e21[i - k]) != (closes[i - k - 1] > e21[i - k - 1]):
                cnt += 1
        if cnt >= 4:
            continue
        sma20 = sum(spread[i - 19:i + 1]) / 20
        if spread[i] <= sma20:
            continue
        if e8[i] > e21[i] and closes[i] > e21[i]:
            out[i] = 1
        elif e8[i] < e21[i] and closes[i] < e21[i]:
            out[i] = -1
    return out


def ema_retest_entry(bar, ema, trend, direction):
    """Wick rule: the bar must touch the EMA and CLOSE holding it.
    bar = (ts, o, h, l, c). Returns entry_px or None."""
    ts, o, h, l, c = bar
    if direction == "long":
        if trend == 1 and l <= ema and c > ema:
            return c
    else:
        if trend == -1 and h >= ema and c < ema:
            return c
    return None


def level_machine(bars5, level, direction, start_i, cutoff_i,
                  start_inside=False):
    """Break-then-retest at a level on 5m bars, wick rule both legs.
    Unless start_inside (the ORB case — the range IS inside), the tape
    must first close at-or-inside the level. After the break, a 5m
    close back through the level before the retest kills the setup.
    Returns (break_i, retest_i) — either may be None."""
    seen_inside = start_inside
    brk = None
    for i in range(start_i, min(cutoff_i, len(bars5))):
        ts, o, h, l, c = bars5[i]
        if direction == "long":
            if brk is None:
                if c <= level:
                    seen_inside = True
                elif seen_inside:
                    brk = i
            else:
                if c < level:
                    return brk, None          # level lost — setup dead
                if l <= level and c > level:
                    return brk, i
        else:
            if brk is None:
                if c >= level:
                    seen_inside = True
                elif seen_inside:
                    brk = i
            else:
                if c > level:
                    return brk, None
                if h >= level and c < level:
                    return brk, i
    return brk, None


def sim_stops(bars1, i_after, entry, direction, struct_px, atr_abs,
              bars5, i5_after, e21_5):
    """Simulate the stop grid from the 1m record. Touch stops exit at
    the stop price; close-rule stops exit at the triggering completed
    5m close; unstopped exits at the last 1m close. bars5/e21_5 are
    the DAY's slices, index-aligned. Returns
    (eod_bps, mfe_bps, mae_bps, {variant: {out, exit_px, bps, r}})."""
    sign = 1.0 if direction == "long" else -1.0
    last_c = bars1[-1][4]
    eod_bps = sign * (last_c / entry - 1) * 1e4
    mfe = mae = 0.0
    hit = {}
    touch = {}
    if struct_px is not None:
        touch["struct"] = struct_px
    touch["pct25"] = entry * (1 - sign * 0.0025)
    touch["pct50"] = entry * (1 - sign * 0.005)
    touch["pct100"] = entry * (1 - sign * 0.01)
    if atr_abs is not None and atr_abs > 0:
        touch["atr1"] = entry - sign * atr_abs
    for ts, o, h, l, c in bars1[i_after:]:
        mfe = max(mfe, sign * ((h if sign > 0 else l) / entry - 1) * 1e4)
        mae = min(mae, sign * ((l if sign > 0 else h) / entry - 1) * 1e4)
        for name, px in list(touch.items()):
            if (l <= px) if sign > 0 else (h >= px):
                hit[name] = {"out": "stopped", "exit_px": round(px, 4)}
                del touch[name]
    for name, px in touch.items():
        hit[name] = {"out": "eod", "exit_px": round(last_c, 4)}
    close_rule = {"ema21x": None}
    if struct_px is not None:
        close_rule["struct_5c"] = None
    for j in range(i5_after, len(bars5)):
        c = bars5[j][4]
        if close_rule.get("struct_5c", True) is None:
            if (c < struct_px) if sign > 0 else (c > struct_px):
                close_rule["struct_5c"] = {"out": "stopped",
                                           "exit_px": round(c, 4)}
        if close_rule["ema21x"] is None:
            if (c < e21_5[j]) if sign > 0 else (c > e21_5[j]):
                close_rule["ema21x"] = {"out": "stopped",
                                        "exit_px": round(c, 4)}
    for name, v in close_rule.items():
        hit[name] = v if v is not None else {"out": "eod",
                                             "exit_px": round(last_c, 4)}
    risk_bps = {"pct25": 25.0, "pct50": 50.0, "pct100": 100.0}
    if struct_px is not None:
        risk_bps["struct"] = abs(entry - struct_px) / entry * 1e4
        risk_bps["struct_5c"] = risk_bps["struct"]
    if atr_abs is not None and atr_abs > 0:
        risk_bps["atr1"] = atr_abs / entry * 1e4
    for name, v in hit.items():
        bps = sign * (v["exit_px"] / entry - 1) * 1e4
        v["bps"] = round(bps, 1)
        rk = risk_bps.get(name)
        v["r"] = round(bps / rk, 2) if rk and rk > 0 else None
    return round(eod_bps, 1), round(mfe, 1), round(mae, 1), hit


# ── the walk ─────────────────────────────────────────────────────────

def _in_window(ts):
    return W_START <= ts.time() <= W_END


def _grade_ticker(conn, ticker, table, done_days, deadline):
    with conn.cursor() as c:
        c.execute(f"SELECT ts, open, high, low, close FROM {table} "
                  f"WHERE ticker=%s ORDER BY ts", (ticker,))
        raw = c.fetchall()
    if len(raw) < 500:
        return 0
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    bars1 = [(ts.astimezone(et), float(o), float(h), float(l), float(cl))
             for ts, o, h, l, cl in raw]
    bars5, last1m = resample5(bars1)
    c1 = [b[4] for b in bars1]
    c5 = [b[4] for b in bars5]
    e8_1, e21_1 = ema_series(c1, 8), ema_series(c1, 21)
    e8_5, e21_5 = ema_series(c5, 8), ema_series(c5, 21)
    tr1 = trend_series(c1, e8_1, e21_1)
    tr5 = trend_series(c5, e8_5, e21_5)
    atr5 = atr_series(bars5, 14)

    day_1m, day_5m = {}, {}
    for i, b in enumerate(bars1):
        day_1m.setdefault(b[0].date(), []).append(i)
    for j, b in enumerate(bars5):
        day_5m.setdefault(b[0].date(), []).append(j)
    days = sorted(day_1m)

    n_rows = 0
    orb_end = (dt.datetime(2000, 1, 1, 9, 30)
               + dt.timedelta(minutes=ORB_MIN)).time()
    for di, d in enumerate(days):
        if time.time() > deadline:
            return n_rows
        if d in done_days or di == 0:
            continue
        i1s = day_1m[d]
        j5s = day_5m.get(d, [])
        if len(i1s) < 300 or len(j5s) < 60:
            continue
        prev = day_1m[days[di - 1]]
        pdh = max(bars1[i][2] for i in prev)
        pdl = min(bars1[i][3] for i in prev)
        orb_bars = [i for i in i1s if bars1[i][0].time() < orb_end]
        orb_h = max(bars1[i][2] for i in orb_bars) if orb_bars else None
        orb_l = min(bars1[i][3] for i in orb_bars) if orb_bars else None

        rows = []

        def emit(family, direction, i1_entry, entry_px, struct_px):
            ts = bars1[i1_entry][0]
            j5 = None                 # last 5m bar completed at decision
            for j in j5s:
                if last1m[j] <= i1_entry:
                    j5 = j
                else:
                    break
            atr_abs = atr5[j5] if j5 is not None else None
            j0 = j5s[0]
            eod, mfe, mae, stops = sim_stops(
                bars1[i1s[0]:i1s[-1] + 1], i1_entry - i1s[0] + 1, entry_px,
                direction, struct_px, atr_abs,
                [bars5[j] for j in j5s],
                (j5 - j0 + 1) if j5 is not None else len(j5s),
                [e21_5[j] for j in j5s])
            rows.append((d, ticker, family, direction, ts,
                         round(entry_px, 4),
                         round(struct_px, 4) if struct_px else None,
                         round(atr_abs / entry_px * 100, 3) if atr_abs else None,
                         eod, mfe, mae, json.dumps(stops)))

        for direction, sgn in (("long", 1), ("short", -1)):
            for fam, ema in (("ema8_5m", e8_5), ("ema21_5m", e21_5)):
                for j in j5s:
                    if not _in_window(bars5[j][0]):
                        continue
                    px = ema_retest_entry(bars5[j], ema[j], tr5[j], direction)
                    if px is not None:
                        struct = bars5[j][3] * (1 - STOP_BUFF) if sgn > 0 \
                            else bars5[j][2] * (1 + STOP_BUFF)
                        emit(fam, direction, last1m[j], px, struct)
                        break
            for i in i1s:
                ts = bars1[i][0]
                if not _in_window(ts):
                    continue
                gov = None            # last 5m bar completed at decision
                for j in j5s:
                    if last1m[j] <= i:
                        gov = j
                    else:
                        break
                if gov is None or tr5[gov] != sgn or tr1[i] != sgn:
                    continue
                px = (ema_retest_entry(bars1[i], e8_1[i], tr1[i], direction)
                      or ema_retest_entry(bars1[i], e21_1[i], tr1[i],
                                          direction))
                if px is not None:
                    struct = bars1[i][3] * (1 - STOP_BUFF) if sgn > 0 \
                        else bars1[i][2] * (1 + STOP_BUFF)
                    emit("ema_1m_gated", direction, i, px, struct)
                    break
            d5 = [bars5[j] for j in j5s]
            k_start = next((k for k, b in enumerate(d5)
                            if _in_window(b[0])), 0)
            k_cut = next((k for k, b in enumerate(d5)
                          if b[0].time() > W_END), len(d5))
            k_orb = next((k for k, b in enumerate(d5)
                          if b[0].time() >= orb_end), len(d5))
            for fam_rt, fam_ch, level, s, inside in (
                    ("orb_rt", "orb_chase", orb_h if sgn > 0 else orb_l,
                     max(k_start, k_orb), True),
                    ("pdh_rt", "pdh_chase", pdh if sgn > 0 else pdl,
                     k_start, False)):
                if level is None:
                    continue
                brk, rt = level_machine(d5, level, direction, s, k_cut,
                                        start_inside=inside)
                if brk is not None:
                    j = j5s[brk]
                    emit(fam_ch, direction, last1m[j], d5[brk][4], None)
                if rt is not None:
                    j = j5s[rt]
                    struct = d5[rt][3] * (1 - STOP_BUFF) if sgn > 0 \
                        else d5[rt][2] * (1 + STOP_BUFF)
                    emit(fam_rt, direction, last1m[j], d5[rt][4], struct)

        with conn.cursor() as c:
            if rows:
                c.executemany("""INSERT INTO tapeentry_events
                    (trade_date, ticker, family, direction, entry_ts,
                     entry_px, struct_px, atr_pct, eod_bps, mfe_bps,
                     mae_bps, stops)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""", rows)
            c.execute("INSERT INTO tapeentry_days (ticker, trade_date, "
                      "n_events) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                      (ticker, d, len(rows)))
        conn.commit()
        n_rows += len(rows)
    return n_rows


def run() -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    deadline = time.time() + BUDGET_S
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("SELECT job_name FROM scheduler_job_claims "
                      "WHERE job_name = ANY(%s)", (list(BARS_MARKERS),))
            bars_done = {r[0] for r in c.fetchall()} == set(BARS_MARKERS)
            c.execute("SELECT ticker, trade_date FROM tapeentry_days")
            done = {}
            for tk, dd in c.fetchall():
                done.setdefault(tk, set()).add(dd)
        total = 0
        pending = False
        for tk in MAG7 + LIQUID:
            table = "mag7_1m_bars" if tk in MAG7 else "liquid_1m_bars"
            total += _grade_ticker(conn, tk, table, done.get(tk, set()),
                                   deadline)
            if time.time() > deadline:
                pending = True
                log.info("[tapeentry] budget hit at %s; resuming.", tk)
                break
        log.info("[tapeentry] wrote %d event(s) this pass.", total)
        if bars_done and not pending and total == 0:
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,))
            conn.commit()
            log.info("[tapeentry] complete — marker %s.", COMPLETE_MARKER)
            return True
        return False
    finally:
        conn.close()
