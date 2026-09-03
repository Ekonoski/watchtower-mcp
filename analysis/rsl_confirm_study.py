"""
The confirmed-runner cut of the RS-leader GO (2026-09-03, 8:07 AM —
Eric: "I thought we were entering these when the tape is confirming a
runner and not a coin flip"). The mechanical GO checks 15 minutes of
relative strength plus one held 1m bar; the confirmation Eric applies
by eye — trend established, higher high, above VWAP, past the opening
flush — was never part of the graded rule. This grades it.

SPEC (frozen before any number):
  entries   the 446 graded GO-pullback entries (rs_leader_events,
            role='leader', entry_kind='go_pullback').
  legs      each readable at the GO bar from the stored 1m RTH bars,
            completed bars only, None inside a warmup (unknown is not
            False):
    trend1m_on   rankladder_study.trend1m_at at the GO bar (imported)
    or15_break   GO close > the high of the first 15 1m bars (9:30-9:44)
    hh5          the last COMPLETED 5m block before the GO's block made
                 the session high (higher than every earlier block)
    above_vwap   GO close > session VWAP through the GO bar (close x vol)
    late         GO bar at/after 10:00 ET
    rs_strong    rs_945_pct >= RS_STRONG (1.0%)
  outcomes  hold-to-close in bps (the graded expression), mfe/mae bps,
            and a MODELED option P&L per contract for the 0.70-delta
            call the 🎯 names: Black-Scholes, r=0, sigma = trailing
            20-day realized vol of daily closes (a stated PROXY —
            no 2-year intraday IV history exists here), expiry = the
            coming Friday's close (0DTE on Fridays), strike solved
            for delta 0.70 at the GO bar, re-priced at the close with
            the day's time decayed. Shares-equivalent (0.70 x move x
            100) beside it so theta+gamma drag is visible. Spread cost
            is a declared HOLE (not modeled). Option fields are None
            when the vol proxy has < 15 closes.
  readout   per leg and the all-legs stack: avg/median bps, win rate,
            avg option $/contract, both year-halves, per-name.

Writes ONLY rsl_confirm_events. Marker rsl_confirm_v1 when
rsleader_study_v1 exists and nothing is ungraded.
"""
import datetime as dt
import json
import logging
import math
import time

from analysis.hybrid_exit_study import _res5 as res5
from analysis.rankladder_study import trend1m_at
from analysis.rsleader_study import ema

log = logging.getLogger("watchtower.rsl_confirm")

COMPLETE_MARKER = "rsl_confirm_v1"
SOURCE_MARKER = "rsleader_study_v1"
BUDGET_S = 12 * 60
RS_STRONG = 1.0
OR_BARS = 15
TARGET_DELTA = 0.70
VOL_LOOKBACK = 20
LEGS = ("trend1m_on", "or15_break", "hh5", "above_vwap", "late", "rs_strong")


# ── pure cores ───────────────────────────────────────────────────────

def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, sigma):
    """Black-Scholes call, r=0. T in years; T<=0 -> intrinsic."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / sq
    d2 = d1 - sq
    return S * _ncdf(d1) - K * _ncdf(d2)


def strike_for_delta(S, T, sigma, delta=TARGET_DELTA):
    """Strike whose BS call delta (N(d1)) equals `delta` at (S, T, sigma)."""
    # inverse normal via bisection on _ncdf — tiny, dependency-free
    lo, hi = -6.0, 6.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _ncdf(mid) < delta:
            lo = mid
        else:
            hi = mid
    d1 = (lo + hi) / 2
    sq = sigma * math.sqrt(T)
    return S * math.exp(-(d1 * sq - 0.5 * sigma * sigma * T))


def realized_vol(closes, n=VOL_LOOKBACK):
    """Annualized close-to-close vol over the last n returns; None if
    fewer than 15 returns exist."""
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]
    rets = rets[-n:]
    if len(rets) < 15:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 252)


def years_to_friday_close(ts):
    """(T_go, T_close): trading-time years from the GO bar to the coming
    Friday's 16:00, and from this day's close to it (0 on a Friday)."""
    days_ahead = (4 - ts.weekday()) % 7          # Mon=0 .. Fri=4
    rem_hours = max(0.0, (16 * 60 - (ts.hour * 60 + ts.minute)) / 60.0)
    t_go = (days_ahead + rem_hours / 6.5) / 252.0
    t_close = days_ahead / 252.0
    return t_go, t_close


def option_model(entry, close, ts, sigma):
    """Modeled 0.70-delta call P&L per contract, GO -> close, beside the
    shares-equivalent. None when the vol proxy is a hole."""
    if sigma is None:
        return None
    t_go, t_close = years_to_friday_close(ts)
    K = strike_for_delta(entry, t_go, sigma)
    c0 = bs_call(entry, K, t_go, sigma)
    c1 = bs_call(close, K, t_close, sigma)
    return {"strike": round(K, 2), "prem_go": round(c0, 3),
            "prem_close": round(c1, 3),
            "opt_pnl": round((c1 - c0) * 100, 2),
            "shares_eq": round(TARGET_DELTA * (close - entry) * 100, 2),
            "sigma": round(sigma, 4), "t_go_days": round(t_go * 252, 2)}


def legs_at(bars, i_go, rs_945):
    """Pure. bars = the day's 1m RTH bars (ts, o, h, l, c, v); i_go =
    the GO bar's index. Every leg reads completed bars through i_go."""
    closes = [b[4] for b in bars]
    e8, e21 = ema(closes, 8), ema(closes, 21)
    go_close = bars[i_go][4]
    out = {"trend1m_on": trend1m_at(closes, e8, e21, i_go)}
    out["or15_break"] = (go_close > max(b[2] for b in bars[:OR_BARS])
                         if i_go >= OR_BARS else None)
    bars5, last5 = res5([b[:5] for b in bars])
    j_go = next((j for j, last in enumerate(last5) if last >= i_go), None)
    if j_go is None or j_go < 2:
        out["hh5"] = None
    else:
        highs = [b[2] for b in bars5[:j_go]]
        out["hh5"] = highs[-1] > max(highs[:-1])
    vol = sum(b[5] for b in bars[:i_go + 1] if b[5] is not None)
    if vol > 0:
        vwap = sum(b[4] * b[5] for b in bars[:i_go + 1]
                   if b[5] is not None) / vol
        out["above_vwap"] = go_close > vwap
    else:
        out["above_vwap"] = None
    ts = bars[i_go][0]
    out["late"] = (ts.hour, ts.minute) >= (10, 0)
    out["rs_strong"] = (float(rs_945) >= RS_STRONG) if rs_945 is not None else None
    return out


def outcomes_at(bars, i_go, entry):
    last_c = bars[-1][4]
    after = bars[i_go + 1:] or [bars[i_go]]
    hi = max(b[2] for b in after)
    lo = min(b[3] for b in after)
    return {"eod_bps": round((last_c / entry - 1) * 1e4, 1),
            "mfe_bps": round((hi / entry - 1) * 1e4, 1),
            "mae_bps": round((lo / entry - 1) * 1e4, 1),
            "close_px": round(last_c, 4)}


# ── the seeder ───────────────────────────────────────────────────────

def run() -> bool:
    from zoneinfo import ZoneInfo
    from screen.reversal_screen import _conn
    et = ZoneInfo("America/New_York")
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (SOURCE_MARKER,))
            src_done = c.fetchone() is not None
            c.execute("""SELECT e.id, e.ticker, e.trade_date, e.entry_ts,
                                e.entry_px, e.rs_945_pct
                         FROM rs_leader_events e
                         WHERE e.role='leader' AND e.entry_kind='go_pullback'
                           AND e.entry_ts IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM rsl_confirm_events v
                                           WHERE v.event_id = e.id)
                         ORDER BY e.ticker, e.trade_date""")
            todo = c.fetchall()
        if not todo:
            if src_done:
                with conn.cursor() as c:
                    c.execute("INSERT INTO scheduler_job_claims (job_name, "
                              "run_date) VALUES (%s, CURRENT_DATE) "
                              "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
                conn.commit()
            return True
        daily = {}
        cur_key, bars = None, []
        n = 0
        for eid, tk, d, ets, entry, rs in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[rsl-confirm] budget hit; resuming.")
                return False
            if tk not in daily:
                with conn.cursor() as c:
                    c.execute("""SELECT trade_date, close FROM daily_prices
                                 WHERE ticker=%s ORDER BY trade_date""", (tk,))
                    daily[tk] = [(r[0], float(r[1])) for r in c.fetchall()]
            if (tk, d) != cur_key:
                with conn.cursor() as c:
                    c.execute("""SELECT ts, open, high, low, close, volume
                                 FROM mag7_1m_bars
                                 WHERE ticker=%s AND trade_date=%s
                                 ORDER BY ts""", (tk, d))
                    bars = [(ts.astimezone(et), float(o), float(h),
                             float(l), float(cl),
                             float(v) if v is not None else None)
                            for ts, o, h, l, cl, v in c.fetchall()]
                cur_key = (tk, d)
            if len(bars) < 60:
                continue
            ets_et = ets.astimezone(et)
            i_go = next((i for i, b in enumerate(bars)
                         if b[0] >= ets_et), None)
            if i_go is None:
                continue
            entry = float(entry)
            legs = legs_at(bars, i_go, rs)
            outc = outcomes_at(bars, i_go, entry)
            prior_closes = [cl for dd, cl in daily[tk] if dd < d]
            outc["option"] = option_model(entry, outc["close_px"],
                                          bars[i_go][0],
                                          realized_vol(prior_closes))
            with conn.cursor() as c:
                c.execute("""INSERT INTO rsl_confirm_events
                             (event_id, ticker, trade_date, legs, outcomes)
                             VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                          (eid, tk, d, json.dumps(legs), json.dumps(outc)))
            conn.commit()
            n += 1
        log.info("[rsl-confirm] graded %d GO entries this pass.", n)
        return False
    finally:
        conn.close()
