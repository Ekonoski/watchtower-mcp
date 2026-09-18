"""
The day-bias early-touch-RECLAIM study (queued 2026-08-25 the day of
the first cancelled_early; ordered 2026-09-18 — Eric's ruling that
day_bias stops counting as a book: 19 sessions, 0 fills, 12 stand-asides
and every armed day cancelled by a pre-10:30 touch. The graded trade is
behaving as graded; its n will not arrive this quarter, so the queued
variant grades on the stored record instead of on the book's silence).

THE QUESTION, pre-registered: on days SPY / QQQ OPENED above the prior
day's high and the book CANCELLED because PDH was touched before 10:30,
does a post-10:30 15m CLOSE back above PDH — a true reclaim, wick rule —
grade positively to the close?

ONE DEFINITION: the day is classified by the book's own `decide` (from
analysis/day_bias.py, imported, never re-implemented) on the stored
index_intraday_bars 15m RTH record; PDH is the prior session's high from
daily_prices, the source the live book's `_pdh` reads. The reclaim is
`reclaim()` below, pure and pinned in tests/test_daybias_reclaim.py.

  event      the FIRST bar starting at/after 10:30 ET whose CLOSE > PDH
             on a cancelled_early day; entry = that bar's close (a price
             that printed; the cancelled book's limit at PDH is NOT the
             entry — the level was already lost once)
  lost_close TRUE when some 15m bar from the touch onward CLOSED at/below
             PDH before the reclaim bar — the level was actually LOST on
             a close and then regained (the true reclaim); FALSE means
             the touch was a wick and price simply held above (recorded
             so the readout can cut on it; the both-halves bar applies
             to the lost_close=true cohort, which is the variant queued)
  no_reclaim a cancelled day on which no bar at/after 10:30 closed
             above PDH — recorded as its own state, never dropped
  outcomes   eod_bps: entry -> the day's true close (daily_prices close
             when present, else the last recorded 15m close — close_src
             says which); mfe/mae bps from the bars after entry; and the
             book's own declared deviation replayed — a 0.75% stop on
             15m CLOSES (stop_hit / stop_exit_px / stop_bps / stop_r on
             the 0.75% unit); the wick rule holds

BAR (frozen before any number): the lost_close reclaim is adopted as a
second declared entry only if it is positive in BOTH eras (2005-2015 /
2016-) on SPY AND replicates in direction on QQQ — the same bar the late
retest cleared (69% / +27 bps / MFE~2xMAE, n=273). Otherwise the cancel
stands vindicated and cancelled_early days stay untraded.

Stated where the numbers surface: closes as fills, 15m granularity, no
costs, underlying bps; QQQ from 2011 only.

Writes ONLY daybias_reclaim_events; marker-retired one-shot
(daybias_reclaim_v1); idempotent via UNIQUE (ticker, trade_date).
"""
import bisect
import datetime as dt
import logging
import time

from analysis.day_bias import DISASTER_STOP_PCT, LATE_START, decide

log = logging.getLogger("watchtower.daybias_reclaim")

COMPLETE_MARKER = "daybias_reclaim_v1"
TICKERS = ("SPY", "QQQ")
ERA_SPLIT = dt.date(2016, 1, 1)
BUDGET_S = 20 * 60

# Migration 075, applied idempotently at run start (the same text; the
# study cannot depend on a hand-applied DDL step — a missing table would
# be one more silent seed skip).
DDL = """
CREATE TABLE IF NOT EXISTS daybias_reclaim_events (
    id            bigserial PRIMARY KEY,
    ticker        text NOT NULL,
    trade_date    date NOT NULL,
    pdh           numeric NOT NULL,
    state         text NOT NULL,
    touch_ts      timestamptz NOT NULL,
    lost_close    boolean,
    reclaim_ts    timestamptz,
    entry_px      numeric,
    day_close_px  numeric,
    close_src     text,
    eod_bps       numeric,
    mfe_bps       numeric,
    mae_bps       numeric,
    stop_hit      boolean,
    stop_exit_px  numeric,
    stop_bps      numeric,
    stop_r        numeric,
    bars_after    integer,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticker, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daybias_reclaim_tk_state
    ON daybias_reclaim_events (ticker, state, lost_close);
"""

READOUT_SQL = """
SELECT ticker,
       CASE WHEN trade_date < '2016-01-01' THEN 'pre2016' ELSE 'post2016' END AS era,
       lost_close, count(*) AS n,
       round(100.0 * avg((eod_bps > 0)::int), 1) AS win_pct,
       round(avg(eod_bps), 1) AS avg_bps,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY eod_bps)::numeric, 1) AS med_bps,
       round(avg(mfe_bps), 1) AS mfe, round(avg(mae_bps), 1) AS mae,
       round(100.0 * avg(stop_hit::int), 1) AS stop_pct,
       round(avg(stop_bps), 1) AS stop_var_bps
FROM daybias_reclaim_events
WHERE state = 'reclaimed'
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
"""


def reclaim(bars, pdh, stop_pct=DISASTER_STOP_PCT, day_close=None):
    """Pure. bars: RTH 15m tuples (ts_et, open, close, high, low, ...)
    oldest-first — the shape day_bias.decide reads. Returns None when the
    day is not a cancelled_early day (the study has nothing to grade),
    else a dict with state 'no_reclaim' or 'reclaimed' and the fields
    documented in the module docstring. day_close overrides the last
    bar's close as the outcome anchor when the caller has the official
    daily close."""
    res = decide(bars, pdh, stop_pct)
    if res.get("state") != "cancelled_early":
        return None
    touch_ts = res["at"]
    i_touch = next(i for i, b in enumerate(bars) if b[0] == touch_ts)
    lost = False
    j = None
    for i in range(i_touch, len(bars)):
        ts, _o, c = bars[i][0], bars[i][1], float(bars[i][2])
        if ts.time() >= LATE_START and c > pdh:
            j = i
            break
        if c <= pdh:
            lost = True
    out = {"pdh": pdh, "touch_ts": touch_ts}
    if j is None:
        out["state"] = "no_reclaim"
        return out
    entry = float(bars[j][2])
    close_px = float(day_close) if day_close is not None else float(bars[-1][2])
    seg = bars[j + 1:]
    stop = entry * (1 - stop_pct)
    stop_hit, stop_exit = False, close_px
    for (_ts, _o, c, _h, _l, *_v) in seg:
        if float(c) <= stop:
            stop_hit, stop_exit = True, float(c)
            break
    mfe = max((float(b[3]) for b in seg), default=entry)
    mae = min((float(b[4]) for b in seg), default=entry)
    out.update({
        "state": "reclaimed", "lost_close": lost, "reclaim_ts": bars[j][0],
        "entry_px": entry, "day_close_px": close_px,
        "eod_bps": round((close_px / entry - 1) * 1e4, 2),
        "mfe_bps": round((mfe / entry - 1) * 1e4, 2),
        "mae_bps": round((mae / entry - 1) * 1e4, 2),
        "stop_hit": stop_hit, "stop_exit_px": stop_exit,
        "stop_bps": round((stop_exit / entry - 1) * 1e4, 2),
        "stop_r": round((stop_exit - entry) / (entry * stop_pct), 3),
        "bars_after": len(seg),
    })
    return out


def _one_ticker(conn, tk, et):
    with conn.cursor() as c:
        c.execute("""SELECT trade_date, ts, open, high, low, close
                     FROM index_intraday_bars WHERE ticker=%s ORDER BY ts""", (tk,))
        rows = c.fetchall()
        c.execute("""SELECT trade_date, high, close FROM daily_prices
                     WHERE ticker=%s AND high IS NOT NULL ORDER BY trade_date""", (tk,))
        daily = c.fetchall()
    if len(rows) < 100 or len(daily) < 10:
        log.warning("[daybias-reclaim] %s: %d bars / %d daily rows — hole, skipped.",
                    tk, len(rows), len(daily))
        return {}
    dates = [r[0] for r in daily]
    highs = {r[0]: float(r[1]) for r in daily}
    closes = {r[0]: (float(r[2]) if r[2] is not None else None) for r in daily}
    by_day = {}
    for d, ts, o, h, l, cl in rows:
        t = ts.astimezone(et)
        if dt.time(9, 30) <= t.time() <= dt.time(15, 45):
            by_day.setdefault(d, []).append((t, float(o), float(cl), float(h), float(l)))
    census = {"days": 0, "no_prior": 0, "cancelled": 0, "reclaimed": 0,
              "no_reclaim": 0, "other": 0}
    events = []
    for d, bars in sorted(by_day.items()):
        census["days"] += 1
        i = bisect.bisect_left(dates, d)
        if i == 0:
            census["no_prior"] += 1
            continue
        pdh = highs[dates[i - 1]]
        day_close = closes.get(d)
        ev = reclaim(bars, pdh, day_close=day_close)
        if ev is None:
            census["other"] += 1
            continue
        census["cancelled"] += 1
        census[ev["state"]] += 1
        close_src = "daily_prices" if day_close is not None else "last_15m_bar"
        events.append((
            tk, d, pdh, ev["state"], ev["touch_ts"], ev.get("lost_close"),
            ev.get("reclaim_ts"), ev.get("entry_px"), ev.get("day_close_px"),
            close_src if ev["state"] == "reclaimed" else None,
            ev.get("eod_bps"), ev.get("mfe_bps"), ev.get("mae_bps"),
            ev.get("stop_hit"), ev.get("stop_exit_px"), ev.get("stop_bps"),
            ev.get("stop_r"), ev.get("bars_after"),
        ))
    if events:
        with conn.cursor() as c:
            c.executemany("""INSERT INTO daybias_reclaim_events
                (ticker, trade_date, pdh, state, touch_ts, lost_close,
                 reclaim_ts, entry_px, day_close_px, close_src, eod_bps,
                 mfe_bps, mae_bps, stop_hit, stop_exit_px, stop_bps, stop_r,
                 bars_after)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, trade_date) DO NOTHING""", events)
        conn.commit()
    log.info("[daybias-reclaim] %s: %s", tk, census)
    return census


def run() -> bool:
    """One-shot with marker retirement; idempotent on re-run. Logs the
    READOUT_SQL result on completion so the verdict is in the boot log."""
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
            c.execute(DDL)
        conn.commit()
        for tk in TICKERS:
            if time.time() - t0 > BUDGET_S:
                log.info("[daybias-reclaim] budget hit; resuming next boot.")
                return False
            _one_ticker(conn, tk, et)
        with conn.cursor() as c:
            c.execute(READOUT_SQL)
            for row in c.fetchall():
                log.info("[daybias-reclaim] readout %s", row)
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                      "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                      (COMPLETE_MARKER,))
        conn.commit()
        log.info("[daybias-reclaim] complete — marker %s.", COMPLETE_MARKER)
        return True
    finally:
        conn.close()
