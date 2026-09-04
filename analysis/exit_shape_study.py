"""
The exit-shape study (2026-09-03 — Eric: "if our options are hitting
20/30/50% during the trade and we end up negative the failure is how we
exit… take profit at areas of resistance including our bot levels as
well as meaningful areas of resistance and then move our SL to break
even… as runners move up we can move our SL up"). The day trader's
frame, graded on the desk's own record.

QUESTION: on the RS-leader GOs and the long tape entries, does taking
profit into strength at the desk's own resistance shelves — half off,
stop to breakeven, runner managed — beat the swing-style exits (hold,
disaster, trail) in stock bps AND in modeled option dollars?

SPEC (frozen before any number):
  populations  rsl_go (446 GO-pullback entries) and tapeentry (every
               long family on the 11 names).
  path map     per trade, from the 1m bars after entry: minutes to the
               best point (MFE) and worst point (MAE), their clock times,
               which prints first, give-back from MFE to the close.
  levels       two families, both known AT ENTRY with no lookahead:
    shelf      the desk's multi-touch shelves — analysis/levels.py's own
               pivots + cluster + star logic (imported, never copied) on
               1W/1D from stored daily bars and 4H/1H/15m/5m resampled
               (day-anchored) from the stored 1m record, each windowed
               strictly before the entry bar. TP1 = the first shelf at
               least MIN_TP_BPS above entry; TP2 = the next.
    naive      PDH, the 15-minute opening-range high, the high of day at
               entry, the next option-strike grid line; the premarket
               high once premarket_range exists (else a hole). Same TP1/
               TP2 selection.
  variants     one declared engine (sim_exit) with switches; partial
               fills at the level's TOUCH (a resting limit's execution
               fact), stops on completed 5m CLOSES unless the flavor says
               touch, the 1% disaster TOUCH always on:
    <fam>_tp1_full        all off at TP1
    <fam>_half_be_close   half at TP1, stop to entry (5m close), bell
    <fam>_half_be_touch   same, breakeven on touch
    <fam>_half_ratchet    half at TP1, then the stop ratchets to the
                          highest completed-5m low (never below entry)
    <fam>_half_tp2        half at TP1, breakeven, rest at TP2 touch
    tgt_50/100/150/200    all off at a fixed +bps touch
    half100_trail         half at +100 bps, rest on the 21-EMA 5m trail
    ema8_after_100        after +100 bps touched, exit on a 5m close
                          below the 5m 8-EMA (the tighter trail)
    mom_macd_after_50     after +50 bps, exit when the 5m MACD histogram
                          falls two completed blocks in a row
    mom_rsi_after_50      after +50 bps, exit when 5m RSI(14) closes
                          under 60 having printed >= 65 (the "falling
                          RSI" read); shelf_half_mom_* = half at the
                          shelf, the runner on the momentum fade
    tstop_30 / tstop_60   flat or red at +30/+60 min -> out at that close
    tx_1100/1200/1400     out at that clock time's close
    hold / dis1 / dt_050  the incumbents (dt_050 via lifecycle_state)
  option frame every variant re-priced as the 0.55 / 0.70 / 0.85-delta
               call (Black-Scholes, realized-vol proxy, coming-Friday
               expiry, legs priced at their own exit minutes; spread a
               declared HOLE). The option frame decides.
  readout      avg/median bps, win rate, avg option $/contract per
               delta, both year-halves, per name; a variant with no
               level renders as no_level (a hole), never a zero.
Writes ONLY exit_shape_events. Marker exit_shape_v1 when both source
markers exist and nothing is ungraded.
"""
import bisect
import datetime as dt
import json
import logging
import time

from analysis.hybrid_exit_study import _ema as ema5
from analysis.hybrid_exit_study import _res5 as res5
from analysis.levels import (PIVOT_LEFT, PIVOT_RIGHT, TIMEFRAMES, _pivots,
                             levels_from_points)
from analysis.riskmgmt_study import MAG7, _bars_table
from analysis.rs_leader_book import lifecycle_state
from analysis.rsl_confirm_study import (bs_call, realized_vol, strike_for_delta,
                                        years_to_friday_close)

log = logging.getLogger("watchtower.exit_shape")

COMPLETE_MARKER = "exit_shape_v1"
SOURCE_MARKERS = ("tapeentry_study_v1", "rsleader_study_v1")
BUDGET_S = 12 * 60
UNIT_PCT = 0.01
DISASTER_PCT = 0.01
MIN_TP_BPS = 40
DELTAS = (0.55, 0.70, 0.85)
RESAMPLE_MIN = {"4H": 240, "1H": 60, "15m": 15, "5m": 5}
EOD = dt.time(15, 59)


# ── resampling & levels as of entry ─────────────────────────────────

def _key(ts, k):
    return (ts.date(), ((ts.hour - 9) * 60 + ts.minute - 30) // k)


def resample(bars, k):
    """Day-anchored k-minute bars from 1m tuples (ts,o,h,l,c[,v]) ->
    [{ts (block start), open, high, low, close}]."""
    out, key = [], None
    for b in bars:
        kk = _key(b[0], k)
        if kk != key:
            out.append({"ts": b[0], "open": b[1], "high": b[2], "low": b[3],
                        "close": b[4]})
            key = kk
        else:
            o = out[-1]
            o["high"] = max(o["high"], b[2])
            o["low"] = min(o["low"], b[3])
            o["close"] = b[4]
    return out


def weekly_from_daily(daily):
    out, key = [], None
    for d in daily:
        wk = d["date"].isocalendar()[:2]
        if wk != key:
            out.append(dict(d))
            key = wk
        else:
            o = out[-1]
            o["high"] = max(o["high"], d["high"])
            o["low"] = min(o["low"], d["low"])
            o["close"] = d["close"]
    return out


def shelves_asof(daily, series, entry_ts, entry_px):
    """daily: [{date, open, high, low, close}] ascending; series: {tf:
    (ts_list, bars_list)} resampled from the 1m record. Everything is
    windowed strictly BEFORE the entry bar."""
    d0 = entry_ts.date()
    points = []
    d_all = [d for d in daily if d["date"] < d0]
    wk_cut = d0 - dt.timedelta(days=TIMEFRAMES["1W"]["days"])
    weekly = weekly_from_daily([d for d in d_all if d["date"] >= wk_cut])
    if len(weekly) >= 25:
        points += _pivots(weekly, "1W", PIVOT_LEFT, PIVOT_RIGHT)
    dd_cut = d0 - dt.timedelta(days=TIMEFRAMES["1D"]["days"])
    d_slice = [d for d in d_all if d["date"] >= dd_cut]
    if len(d_slice) >= 40:
        points += _pivots(d_slice, "1D", PIVOT_LEFT, PIVOT_RIGHT)
    for tf, (ts_list, bars) in series.items():
        cut = entry_ts - dt.timedelta(days=TIMEFRAMES[tf]["days"])
        i0 = bisect.bisect_left(ts_list, cut)
        i1 = bisect.bisect_left(ts_list, entry_ts)
        sl = bars[i0:i1]
        if len(sl) >= 25:
            points += _pivots(sl, tf, PIVOT_LEFT, PIVOT_RIGHT)
    if len(d_slice) < 40 or not points:
        return None
    return levels_from_points(points, d_slice, entry_px)


def pick_targets(levels_up, entry):
    """First level >= entry*(1+MIN_TP_BPS) and the next above it."""
    floor = entry * (1 + MIN_TP_BPS / 1e4)
    ups = sorted((lv for lv in levels_up if lv["price"] >= floor), key=lambda lv: lv["price"])
    return (ups[0] if ups else None, ups[1] if len(ups) > 1 else None)


def strike_step(px):
    return 5.0 if px >= 200 else (2.5 if px >= 100 else 1.0)


def naive_levels(bars, i, entry, pdh, pmh):
    out = {"pdh": pdh, "pmh": pmh}
    out["orh"] = max(b[2] for b in bars[:15]) if i >= 15 else None
    out["hod"] = max(b[2] for b in bars[:i]) if i > 0 else None
    step = strike_step(entry)
    floor = entry * (1 + MIN_TP_BPS / 1e4)
    out["strike"] = (int(floor / step) + 1) * step
    return out


# ── the exit engine ──────────────────────────────────────────────────

def rsi_series(closes, n=14):
    """Wilder RSI on a list; None inside the warmup."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[k] - closes[k - 1], 0) for k in range(1, len(closes))]
    losses = [max(closes[k - 1] - closes[k], 0) for k in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
    for k in range(n + 1, len(closes)):
        ag = (ag * (n - 1) + gains[k - 1]) / n
        al = (al * (n - 1) + losses[k - 1]) / n
        out[k] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
    return out


def macd_hist(closes, fast=12, slow=26, sig=9):
    line = [a - b for a, b in zip(ema5(closes, fast), ema5(closes, slow))]
    return [m - s for m, s in zip(line, ema5(line, sig))]


def sim_exit(bars, i, entry, *, tp1=None, tp1_frac=1.0, be=None, ratchet=None,
             tp2=None, tail="bell", arm_bps=None, tstop_min=None, tx_time=None,
             mom=None):
    """Pure. One engine, declared switches. Returns {out, legs:
    [(frac, px, ts)], exit_px (weighted), bps, r}. Order inside a bar:
    disaster touch, touch-stop, TP1, TP2, target/time exits, then the
    5m-close decisions at a completed block. `mom` = a momentum-fade
    exit on the remainder once armed: 'macd' = the 5m MACD(12,26,9)
    histogram lower for two consecutive completed blocks; 'rsi' = the 5m
    RSI(14) closes below 60 after having printed >= 65 since arming
    (Eric, 2026-09-03: "falling RSIs and MACD cross overs as the trade
    is happening")."""
    disaster = entry * (1 - DISASTER_PCT)
    unit = entry * UNIT_PCT
    bars5, last5 = res5([b[:5] for b in bars])
    closes5 = [b[4] for b in bars5]
    e21_5, e8_5 = ema5(closes5, 21), ema5(closes5, 8)
    hist5, rsi5 = macd_hist(closes5), rsi_series(closes5)
    done_by_min = {}
    for j in range(len(bars5)):
        ts_j = bars[last5[j]][0]
        if j < len(bars5) - 1 or ((ts_j.hour - 9) * 60 + ts_j.minute - 30) % 5 == 4:
            done_by_min[last5[j]] = (e21_5[j], e8_5[j], bars5[j][3], j)
    rsi_peak = None
    legs, remaining, out = [], 1.0, []
    stop, stop_mode = None, "close"
    tp1_hit = False
    # tails/momentum exits act only once the trade has proven itself:
    # after TP1 when a level is set, after the arm touch when arm_bps is
    # set, and from the start only when neither is declared
    armed = arm_bps is None and tp1 is None
    t_entry = bars[i][0]
    for k in range(i + 1, len(bars)):
        ts, o, h, l, c = bars[k][:5]
        if remaining <= 0:
            break
        if l <= disaster:
            legs.append((remaining, disaster, ts)); out.append("disaster"); remaining = 0; break
        if stop is not None and stop_mode == "touch" and l <= stop:
            legs.append((remaining, stop, ts)); out.append("stop_touch"); remaining = 0; break
        if tp1 is not None and not tp1_hit and h >= tp1:
            frac = min(tp1_frac, remaining)
            legs.append((frac, tp1, ts)); out.append("tp1"); remaining -= frac; tp1_hit = True
            if be:
                stop, stop_mode = entry, be
            armed = True
            if remaining <= 0:
                break
        if tp2 is not None and tp1_hit and remaining > 0 and h >= tp2:
            legs.append((remaining, tp2, ts)); out.append("tp2"); remaining = 0; break
        if arm_bps is not None and not armed and h >= entry * (1 + arm_bps / 1e4):
            armed = True
        if tstop_min is not None and (ts - t_entry) >= dt.timedelta(minutes=tstop_min) and c <= entry:
            legs.append((remaining, c, ts)); out.append("tstop"); remaining = 0; break
        if tx_time is not None and ts.time() >= tx_time:
            legs.append((remaining, c, ts)); out.append("tx"); remaining = 0; break
        blk = done_by_min.get(k)
        if blk is not None:
            e21, e8, lo5, j = blk
            if stop is not None and stop_mode == "close" and c < stop:
                legs.append((remaining, c, ts)); out.append("stop_close"); remaining = 0; break
            if armed and tail == "trail21" and c < e21:
                legs.append((remaining, c, ts)); out.append("trail21"); remaining = 0; break
            if armed and tail == "ema8" and c < e8:
                legs.append((remaining, c, ts)); out.append("ema8"); remaining = 0; break
            if armed and mom == "macd" and j >= 2 and hist5[j] < hist5[j - 1] < hist5[j - 2]:
                legs.append((remaining, c, ts)); out.append("mom_macd"); remaining = 0; break
            if armed and mom == "rsi" and rsi5[j] is not None:
                if rsi5[j] >= 65:
                    rsi_peak = max(rsi_peak or 0, rsi5[j])
                if rsi_peak is not None and rsi5[j] < 60:
                    legs.append((remaining, c, ts)); out.append("mom_rsi"); remaining = 0; break
            if ratchet == "5mlow" and tp1_hit:
                stop = max(stop or entry, lo5)
    if remaining > 0:
        legs.append((remaining, bars[-1][4], bars[-1][0])); out.append("bell")
    px = sum(f * p for f, p, _ in legs)
    return {"out": "+".join(out), "legs": legs, "exit_px": round(px, 4),
            "bps": round((px / entry - 1) * 1e4, 1), "r": round((px - entry) / unit, 3)}


def path_map(bars, i, entry):
    after = bars[i + 1:]
    if not after:
        return None
    hi_k = max(range(len(after)), key=lambda k: after[k][2])
    lo_k = min(range(len(after)), key=lambda k: after[k][3])
    mfe = (after[hi_k][2] / entry - 1) * 1e4
    mae = (after[lo_k][3] / entry - 1) * 1e4
    eod = (after[-1][4] / entry - 1) * 1e4
    return {"mfe_bps": round(mfe, 1), "mae_bps": round(mae, 1), "eod_bps": round(eod, 1),
            "min_to_mfe": hi_k + 1, "min_to_mae": lo_k + 1,
            "t_mfe": after[hi_k][0].strftime("%H:%M"), "t_mae": after[lo_k][0].strftime("%H:%M"),
            "first": "mfe" if hi_k < lo_k else ("mae" if lo_k < hi_k else "same"),
            "giveback_bps": round(mfe - eod, 1)}


def option_frame(entry, t_entry, legs, sigma):
    """Per delta: P&L per contract for the legs at their own exit
    minutes. None when the vol proxy is a hole."""
    if sigma is None:
        return None
    t0, _ = years_to_friday_close(t_entry)
    out = {}
    for d in DELTAS:
        K = strike_for_delta(entry, t0, sigma, d)
        p0 = bs_call(entry, K, t0, sigma)
        pnl = 0.0
        for frac, px, ts in legs:
            t, _ = years_to_friday_close(ts)
            pnl += frac * (bs_call(px, K, t, sigma) - p0) * 100
        out[f"{d:.2f}"] = round(pnl, 2)
    return out


def variants_for(bars, i, entry, tp):
    """tp: {'shelf': (tp1, tp2), 'naive': (tp1, tp2)} prices or None."""
    v = {}
    for fam, (t1, t2) in tp.items():
        if t1 is None:
            for name in ("tp1_full", "half_be_close", "half_be_touch", "half_ratchet", "half_tp2"):
                v[f"{fam}_{name}"] = {"out": "no_level"}
            continue
        v[f"{fam}_tp1_full"] = sim_exit(bars, i, entry, tp1=t1)
        v[f"{fam}_half_be_close"] = sim_exit(bars, i, entry, tp1=t1, tp1_frac=0.5, be="close")
        v[f"{fam}_half_be_touch"] = sim_exit(bars, i, entry, tp1=t1, tp1_frac=0.5, be="touch")
        v[f"{fam}_half_ratchet"] = sim_exit(bars, i, entry, tp1=t1, tp1_frac=0.5, be="close",
                                            ratchet="5mlow")
        v[f"{fam}_half_tp2"] = (sim_exit(bars, i, entry, tp1=t1, tp1_frac=0.5, be="close", tp2=t2)
                                if t2 is not None else {"out": "no_level"})
    for bps in (50, 100, 150, 200):
        v[f"tgt_{bps}"] = sim_exit(bars, i, entry, tp1=entry * (1 + bps / 1e4))
    v["half100_trail"] = sim_exit(bars, i, entry, tp1=entry * 1.01, tp1_frac=0.5, be="close",
                                  tail="trail21")
    v["ema8_after_100"] = sim_exit(bars, i, entry, tail="ema8", arm_bps=100)
    v["mom_macd_after_50"] = sim_exit(bars, i, entry, mom="macd", arm_bps=50)
    v["mom_rsi_after_50"] = sim_exit(bars, i, entry, mom="rsi", arm_bps=50)
    t1 = tp["shelf"][0]
    if t1 is not None:
        v["shelf_half_mom_macd"] = sim_exit(bars, i, entry, tp1=t1, tp1_frac=0.5, be="close", mom="macd")
        v["shelf_half_mom_rsi"] = sim_exit(bars, i, entry, tp1=t1, tp1_frac=0.5, be="close", mom="rsi")
    else:
        v["shelf_half_mom_macd"] = v["shelf_half_mom_rsi"] = {"out": "no_level"}
    v["tstop_30"] = sim_exit(bars, i, entry, tstop_min=30)
    v["tstop_60"] = sim_exit(bars, i, entry, tstop_min=60)
    for hh in (11, 12, 14):
        v[f"tx_{hh:02d}00"] = sim_exit(bars, i, entry, tx_time=dt.time(hh, 0))
    v["hold"] = sim_exit(bars, i, entry)          # disaster only, bell (= dis1)
    v["dis1"] = dict(v["hold"])
    st = lifecycle_state([b[:5] for b in bars], i, entry, entry * 0.995, struct_stop=False)
    if st["exit"] is None:
        v["dt_050"] = sim_exit(bars, i, entry)
    else:
        reason, ts, px = st["exit"]
        unit = entry * UNIT_PCT
        v["dt_050"] = {"out": reason, "legs": [(1.0, px, ts)], "exit_px": round(px, 4),
                       "bps": round((px / entry - 1) * 1e4, 1), "r": round((px - entry) / unit, 3)}
    return v


def grade_entry(bars, i, entry, *, daily, series, pdh, pmh, sigma):
    entry_ts = bars[i][0]
    lv = shelves_asof(daily, series, entry_ts, entry)
    shelf_tp = pick_targets(lv["resistance"], entry) if lv else (None, None)
    nv = naive_levels(bars, i, entry, pdh, pmh)
    naive_up = [{"price": p, "kind": k} for k, p in nv.items() if p is not None]
    naive_tp = pick_targets(naive_up, entry)
    tp = {"shelf": (shelf_tp[0]["price"] if shelf_tp[0] else None,
                    shelf_tp[1]["price"] if shelf_tp[1] else None),
          "naive": (naive_tp[0]["price"] if naive_tp[0] else None,
                    naive_tp[1]["price"] if naive_tp[1] else None)}
    variants = variants_for(bars, i, entry, tp)
    for name, res in variants.items():
        if "legs" in res and "opt" not in res:
            res["opt"] = option_frame(entry, entry_ts, res["legs"], sigma)
            res["legs"] = [(round(f, 3), round(p, 4), ts.strftime("%H:%M")) for f, p, ts in res["legs"]]
    levels = {"shelf_tp1": shelf_tp[0], "shelf_tp2": shelf_tp[1],
              "naive": nv, "naive_tp1": naive_tp[0], "naive_tp2": naive_tp[1],
              "n_resistance": len(lv["resistance"]) if lv else None,
              "tol_pct": lv["tolerance_pct"] if lv else None}
    return path_map(bars, i, entry), levels, variants


# ── the seeder ───────────────────────────────────────────────────────

def _load_ticker(conn, tk, et):
    with conn.cursor() as c:
        c.execute("""SELECT trade_date, open, high, low, close FROM daily_prices
                     WHERE ticker=%s AND open IS NOT NULL ORDER BY trade_date""", (tk,))
        daily = [{"date": r[0], "open": float(r[1]), "high": float(r[2]),
                  "low": float(r[3]), "close": float(r[4])} for r in c.fetchall()]
        c.execute(f"""SELECT ts, open, high, low, close FROM {_bars_table(tk)}
                      WHERE ticker=%s ORDER BY ts""", (tk,))
        m1 = [(ts.astimezone(et), float(o), float(h), float(l), float(cl))
              for ts, o, h, l, cl in c.fetchall()]
        c.execute("SELECT to_regclass('premarket_range')")
        pm = {}
        if c.fetchone()[0]:
            c.execute("SELECT trade_date, pm_high FROM premarket_range WHERE ticker=%s", (tk,))
            pm = {r[0]: float(r[1]) for r in c.fetchall() if r[1] is not None}
    series = {}
    for tf, k in RESAMPLE_MIN.items():
        rs = resample(m1, k)
        series[tf] = ([b["ts"] for b in rs], rs)
    by_day = {}
    for b in m1:
        by_day.setdefault(b[0].date(), []).append(b)
    return daily, series, by_day, pm


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
            c.execute("SELECT count(*) FROM scheduler_job_claims WHERE job_name = ANY(%s)",
                      (list(SOURCE_MARKERS),))
            src_done = c.fetchone()[0] == len(SOURCE_MARKERS)
            c.execute("""SELECT 'rsl_go', e.id, e.ticker, e.trade_date, e.entry_ts, e.entry_px
                         FROM rs_leader_events e
                         WHERE e.role='leader' AND e.entry_kind='go_pullback' AND e.entry_ts IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM exit_shape_events v
                                           WHERE v.source='rsl_go' AND v.event_id=e.id)
                         UNION ALL
                         SELECT 'tapeentry', t.id, t.ticker, t.trade_date, t.entry_ts, t.entry_px
                         FROM tapeentry_events t
                         WHERE t.direction='long' AND t.entry_ts IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM exit_shape_events v
                                           WHERE v.source='tapeentry' AND v.event_id=t.id)
                         ORDER BY 3, 4""")
            todo = c.fetchall()
        if not todo:
            if src_done:
                with conn.cursor() as c:
                    c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                              "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
                conn.commit()
            return True
        cur_tk, loaded = None, None
        n = 0
        for source, eid, tk, d, ets, entry in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[exit-shape] budget hit; resuming (%d graded).", n)
                return False
            if tk != cur_tk:
                loaded = _load_ticker(conn, tk, et)
                cur_tk = tk
            daily, series, by_day, pm = loaded
            bars = by_day.get(d, [])
            if len(bars) < 60:
                continue
            ets_et = ets.astimezone(et)
            i = next((k for k, b in enumerate(bars) if b[0] >= ets_et), None)
            if i is None or i >= len(bars) - 1:
                continue
            entry = float(entry)
            prior = [x for x in daily if x["date"] < d]
            pdh = prior[-1]["high"] if prior else None
            sigma = realized_vol([x["close"] for x in prior])
            path, levels, variants = grade_entry(bars, i, entry, daily=daily, series=series,
                                                 pdh=pdh, pmh=pm.get(d), sigma=sigma)
            with conn.cursor() as c:
                c.execute("""INSERT INTO exit_shape_events
                             (source, event_id, ticker, trade_date, path, levels, variants)
                             VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                          (source, eid, tk, d, json.dumps(path), json.dumps(levels, default=str),
                           json.dumps(variants, default=str)))
            conn.commit()
            n += 1
        log.info("[exit-shape] graded %d entries this pass.", n)
        return False
    finally:
        conn.close()
