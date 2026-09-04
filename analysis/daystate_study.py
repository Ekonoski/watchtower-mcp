"""
The day-state conditioning grid (2026-09-03 — Eric: "let's set this up
for tonight"). Every intraday study so far graded entries across all
days; if intraday persistence exists it lives in a subset of days. This
stamps each graded entry with the day-states that were knowable by
9:45 ET with NO lookahead, so the readout can cut every population by
them and look for STAND-ASIDE days — the kind of edge theta cannot
touch, because the other days are simply not traded.

SPEC (frozen before any number):
  populations  rsl_go (446 GO entries), tapeentry (long families, 11
               names) — outcomes joined from riskmgmt_events at readout
               (hold bps and the dt_050 managed exit); daybias (SPY and
               QQQ days re-decided by day_bias.decide on the stored 15m
               record: state, PDH fill, close-to-close bps).
  legs (all from stored daily / index data as of the prior close or
  the 9:30 open):
    open_state    open > PDH / inside / < PDL (the name's own range)
    gap_bucket    |open/prev_close-1|: <0.3, 0.3-1, 1-2, 2+ (%), signed
    prev_close_pos  prior close in the top/bottom fifth of its range
    spy_trend     SPY prior close above/below its 20-day EMA; 5-day sign
    vix           prior-close level bucket <15/15-20/20-30/30+, one-day
                  change sign, VIX-VIX3M term (backwardation flag)
    sector        the name's sector rank_1m bucket (1-4/5-8/9+) and
                  rs_1w sign from sector_rs_daily (prior day); None for
                  index names
    gamma_regime  gex_levels regime for the name (SPY's as fallback),
                  2026-07-15 onward only — exploratory, n stated
    weekday
  readout   per leg value: avg/median bps, win rate, both year-halves,
            per name; the bar is both halves AND per-name replication;
            cells under n=40 render small-n. Missing legs are None
            (holes), never a bucket.
Writes ONLY daystate_legs. Marker daystate_v1 when riskmgmt_v1 exists
and nothing is ungraded.
"""
import bisect
import datetime as dt
import json
import logging
import time

from analysis.day_bias import decide as daybias_decide

log = logging.getLogger("watchtower.daystate")

COMPLETE_MARKER = "daystate_v1"
SOURCE_MARKER = "riskmgmt_v1"
BUDGET_S = 12 * 60
INDEX_NAMES = ("SPY", "QQQ", "IWM", "DIA")
DAYBIAS_SLOT = {"SPY": 0, "QQQ": 1}
GAMMA_FROM = dt.date(2026, 7, 15)


# ── pure legs ────────────────────────────────────────────────────────

def ema(vals, n):
    out, k = [], 2 / (n + 1)
    e = vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def gap_bucket(pct):
    a = abs(pct)
    b = "a<0.3" if a < 0.3 else "b0.3-1" if a < 1 else "c1-2" if a < 2 else "d2+"
    return f"{'+' if pct >= 0 else '-'}{b}"


def vix_bucket(v):
    return "a<15" if v < 15 else "b15-20" if v < 20 else "c20-30" if v < 30 else "d30+"


def legs_for(day, prev, prev2, spy_prev_close, spy_ema20, spy_5d_ret, vix_row,
             vix_prev_row, sector_row, gamma_regime):
    """day/prev/prev2: {open, high, low, close} of the name's daily
    bars (day = the trade date; only its OPEN is read). Any missing
    input renders None for its legs."""
    out = {}
    if day and prev:
        o = day["open"]
        out["open_state"] = ("above_pdh" if o > prev["high"] else
                             "below_pdl" if o < prev["low"] else "inside")
        out["gap_bucket"] = gap_bucket((o / prev["close"] - 1) * 100)
        rng = prev["high"] - prev["low"]
        pos = (prev["close"] - prev["low"]) / rng if rng > 0 else None
        out["prev_close_pos"] = (None if pos is None else
                                 "top20" if pos >= 0.8 else "bottom20" if pos <= 0.2 else "mid")
        out["prev_day_dir"] = ("up" if prev2 and prev["close"] > prev2["close"] else
                               "down" if prev2 else None)
    else:
        out.update({"open_state": None, "gap_bucket": None, "prev_close_pos": None,
                    "prev_day_dir": None})
    out["spy_above_ema20"] = (None if spy_prev_close is None or spy_ema20 is None
                              else spy_prev_close > spy_ema20)
    out["spy_5d"] = None if spy_5d_ret is None else ("up" if spy_5d_ret > 0 else "down")
    if vix_row:
        out["vix_bucket"] = vix_bucket(vix_row["vix"])
        out["vix_chg"] = (None if not vix_prev_row else
                          "up" if vix_row["vix"] > vix_prev_row["vix"] else "down")
        out["vix_backwardated"] = (None if vix_row.get("vix3m") is None
                                   else vix_row["vix"] > vix_row["vix3m"])
    else:
        out.update({"vix_bucket": None, "vix_chg": None, "vix_backwardated": None})
    if sector_row:
        r = sector_row["rank_1m"]
        out["sector_rank"] = None if r is None else ("top" if r <= 4 else "mid" if r <= 8 else "bottom")
        out["sector_1w"] = (None if sector_row["rs_1w"] is None else
                            "up" if sector_row["rs_1w"] > 0 else "down")
    else:
        out.update({"sector_rank": None, "sector_1w": None})
    out["gamma_regime"] = gamma_regime
    return out


# ── the seeder ───────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, conn, et):
        self.conn, self.et = conn, et
        self.daily = {}
        with conn.cursor() as c:
            c.execute("SELECT trade_date, close FROM daily_prices WHERE ticker='SPY' "
                      "AND close IS NOT NULL ORDER BY trade_date")
            rows = c.fetchall()
            self.spy_dates = [r[0] for r in rows]
            closes = [float(r[1]) for r in rows]
            self.spy_close = dict(zip(self.spy_dates, closes))
            self.spy_ema20 = dict(zip(self.spy_dates, ema(closes, 20)))
            c.execute("SELECT as_of, vix, vix3m FROM vix_history ORDER BY as_of")
            vrows = c.fetchall()
            self.vix_dates = [r[0] for r in vrows]
            self.vix = {r[0]: {"vix": float(r[1]), "vix3m": float(r[2]) if r[2] is not None else None}
                        for r in vrows}
            c.execute("SELECT ticker, sector FROM tickers WHERE sector IS NOT NULL")
            self.sector = dict(c.fetchall())
            c.execute("SELECT trade_date, sector, rank_1m, rs_1w FROM sector_rs_daily")
            self.sector_rs = {(r[0], r[1]): {"rank_1m": r[2], "rs_1w": float(r[3]) if r[3] is not None else None}
                              for r in c.fetchall()}
            c.execute("SELECT ticker, as_of, regime FROM gex_levels WHERE regime IS NOT NULL")
            self.gamma = {(r[0], r[1]): r[2] for r in c.fetchall()}

    def daily_for(self, tk):
        if tk not in self.daily:
            with self.conn.cursor() as c:
                c.execute("""SELECT trade_date, open, high, low, close FROM daily_prices
                             WHERE ticker=%s AND open IS NOT NULL ORDER BY trade_date""", (tk,))
                rows = c.fetchall()
            self.daily[tk] = ([r[0] for r in rows],
                              [{"open": float(r[1]), "high": float(r[2]),
                                "low": float(r[3]), "close": float(r[4])} for r in rows])
        return self.daily[tk]

    def _prev(self, dates, d, back=1):
        i = bisect.bisect_left(dates, d) - back
        return dates[i] if i >= 0 else None

    def legs(self, tk, d):
        dates, rows = self.daily_for(tk)
        i = bisect.bisect_left(dates, d)
        day = rows[i] if i < len(dates) and dates[i] == d else None
        prev = rows[i - 1] if i >= 1 else None
        prev2 = rows[i - 2] if i >= 2 else None
        sp = self._prev(self.spy_dates, d)
        sp5 = self._prev(self.spy_dates, d, 6)
        spy_5d = ((self.spy_close[sp] / self.spy_close[sp5] - 1) if sp and sp5 else None)
        vp = self._prev(self.vix_dates, d)
        vp2 = self._prev(self.vix_dates, d, 2)
        sec = self.sector.get(tk)
        sec_row = self.sector_rs.get((sp, sec)) if sec and sp else None
        regime = None
        if d >= GAMMA_FROM:
            regime = self.gamma.get((tk, d)) or self.gamma.get(("SPY", d))
        out = legs_for(day, prev, prev2,
                       self.spy_close.get(sp) if sp else None,
                       self.spy_ema20.get(sp) if sp else None, spy_5d,
                       self.vix.get(vp) if vp else None,
                       self.vix.get(vp2) if vp2 else None,
                       sec_row, regime)
        out["weekday"] = d.weekday()
        out["sector"] = sec
        return out


def _daybias_days(ctx, tk):
    """Re-decide every stored SPY/QQQ day with day_bias.decide on the
    15m record; outcome = PDH fill -> true close (or the stop)."""
    with ctx.conn.cursor() as c:
        c.execute("""SELECT trade_date, ts, open, high, low, close, volume
                     FROM index_intraday_bars WHERE ticker=%s ORDER BY ts""", (tk,))
        rows = c.fetchall()
    by_day = {}
    for d, ts, o, h, l, cl, v in rows:
        t = ts.astimezone(ctx.et)
        if dt.time(9, 30) <= t.time() <= dt.time(15, 45):
            by_day.setdefault(d, []).append((t, float(o), float(cl), float(h), float(l),
                                             float(v) if v is not None else None))
    dates, drows = ctx.daily_for(tk)
    out = []
    for d, bars in sorted(by_day.items()):
        i = bisect.bisect_left(dates, d)
        if i == 0 or i >= len(dates) or dates[i] != d:
            continue
        pdh = drows[i - 1]["high"]
        res = daybias_decide(bars, pdh)
        state = res["state"]
        if state == "waiting":
            state = "no_retest"
        outc = {"state": state, "pdh": pdh}
        if state in ("filled", "stopped"):
            exit_px = res["stop_px"] if state == "stopped" else drows[i]["close"]
            outc.update({"fill_px": pdh, "exit_px": exit_px,
                         "eod_bps": round((exit_px / pdh - 1) * 1e4, 1),
                         "fill_at": res["at"].strftime("%H:%M")})
        # event_id must be unique per (source, event_id): date x 10 + a
        # ticker slot. First pass used the bare date for both names and
        # QQQ's rows collided with SPY's (ON CONFLICT DO NOTHING ate
        # every one — a silent hole, 2026-09-04).
        out.append((int(d.strftime("%Y%m%d")) * 10 + DAYBIAS_SLOT[tk], d, outc))
    return out


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
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (SOURCE_MARKER,))
            src_done = c.fetchone() is not None
            c.execute("""SELECT r.source, r.event_id, r.ticker, r.trade_date
                         FROM riskmgmt_events r
                         WHERE NOT EXISTS (SELECT 1 FROM daystate_legs v
                                           WHERE v.source=r.source AND v.event_id=r.event_id)
                         ORDER BY r.ticker, r.trade_date""")
            todo = c.fetchall()
            c.execute("SELECT ticker, count(*) FROM daystate_legs WHERE source='daybias' GROUP BY ticker")
            daybias_have = dict(c.fetchall())
            missing_daybias = [tk for tk in DAYBIAS_SLOT if not daybias_have.get(tk)]
        if not todo and not missing_daybias:
            if src_done:
                with conn.cursor() as c:
                    c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                              "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
                conn.commit()
            return True
        ctx = _Ctx(conn, et)
        n = 0
        for source, eid, tk, d in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[daystate] budget hit; resuming (%d).", n)
                return False
            legs = ctx.legs(tk, d)
            with conn.cursor() as c:
                c.execute("""INSERT INTO daystate_legs (source, event_id, ticker, trade_date, legs, outcomes)
                             VALUES (%s,%s,%s,%s,%s,NULL) ON CONFLICT DO NOTHING""",
                          (source, eid, tk, d, json.dumps(legs)))
            n += 1
            if n % 500 == 0:
                conn.commit()
        conn.commit()
        if missing_daybias:
            m = 0
            for tk in missing_daybias:
                for eid, d, outc in _daybias_days(ctx, tk):
                    legs = ctx.legs(tk, d)
                    with conn.cursor() as c:
                        c.execute("""INSERT INTO daystate_legs (source, event_id, ticker, trade_date, legs, outcomes)
                                     VALUES ('daybias',%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                  (eid, tk, d, json.dumps(legs), json.dumps(outc)))
                    m += 1
                conn.commit()
            log.info("[daystate] day-bias days stamped: %d", m)
        log.info("[daystate] stamped %d entries this pass.", n)
        return False
    finally:
        conn.close()
