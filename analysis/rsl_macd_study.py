"""
The MACD-extension leg of the RS-leader GO (2026-09-09 evening — Eric
skipped the TSLA 🎯 on purpose: "it did not have the juice for a
continued move at that point. the MACD was above the zero line on the
1 min and the 5 min and 15 min even in pre-market and when I've seen
that in the past it does not have a lot of juice left"). The desk's GO
was stopped at 9:54 for −1.77R — the third skip in a row the desk lost
on. The eye named a leg; the record grades it.

SPEC (frozen before any number):
  entries   the graded GO-pullback entries (rs_leader_events,
            role='leader', entry_kind='go_pullback', entry_ts set) —
            the rsl_confirm population.
  leg       the MACD(12,26,9) LINE (EMA12 − EMA26 of closes) at the GO
            bar on three timeframes, each on the CONTINUOUS RTH series
            across days so the slow timeframes have their warmup
            (premarket bars are not stored: the "even in premarket"
            clause is eye-only and stated wherever the numbers surface):
              1m   closes through the GO bar inclusive — it has closed
              5m / 15m   closes of COMPLETED blocks anchored at 9:30;
                   the block holding the GO bar counts only if the GO
                   bar is its final minute (the completed-block rule)
            Stored per timeframe: line, signal, hist. n_above = how many
            of the three lines sit above zero; extended = all three. A
            timeframe with fewer than 35 closes is None and n_above is
            None — unknown is never False.
  outcomes  hold-to-close bps (rsl_confirm_study.outcomes_at, imported),
            r_close on the GO risk unit, and r_book through the live
            book's lifecycle_state (imported, never restated).
  readout   by n_above (0..3) and extended vs not: n, avg / median bps,
            win rate, avg r_close, avg r_book; halves split 2025-09-01;
            per name for extended vs not. Bar, frozen: both halves AND
            at least 5 of 7 names; cells under 40 render small-n. If it
            grades, it becomes a WARNING line on the 🎯 — never a gate
            until the live book's own n says so.

Writes ONLY rsl_macd_events. Marker rsl_macd_v1 once rsleader_study_v1
exists and nothing is ungraded.
"""
import datetime as dt
import json
import logging
import time

from analysis.rs_leader_book import lifecycle_state
from analysis.rsl_confirm_study import outcomes_at
from analysis.rsleader_study import ema

log = logging.getLogger("watchtower.rsl_macd")

COMPLETE_MARKER = "rsl_macd_v1"
SOURCE_MARKER = "rsleader_study_v1"
BUDGET_S = 12 * 60
FAST, SLOW, SIG = 12, 26, 9
WARMUP = SLOW + SIG                 # 35 closes before a reading exists
LOOKBACK_1M = 3000                  # ~7.7 sessions of 1m bars feeds the 15m warmup
TIMEFRAMES = ((1, "1m"), (5, "5m"), (15, "15m"))
HALF_SPLIT = dt.date(2025, 9, 1)


# ── pure cores ───────────────────────────────────────────────────────

def macd(closes, fast=FAST, slow=SLOW, sig=SIG):
    """Pure. (line, signal, hist) at the LAST close, EMAs seeded at the
    first value exactly as rsleader_study.ema seeds them. None when
    fewer than slow+sig closes exist (warmup — a hole, never a zero)."""
    if len(closes) < slow + sig:
        return None
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [a - b for a, b in zip(ef, es)]
    signal = ema(line, sig)
    return (round(line[-1], 5), round(signal[-1], 5),
            round(line[-1] - signal[-1], 5))


def _offset(ts):
    return (ts.hour - 9) * 60 + ts.minute - 30


def block_closes(bars, minutes):
    """Pure. bars = [(ts, o, h, l, c, ...)] in order, possibly spanning
    days, ending at the bar being read. Returns the closes of COMPLETED
    blocks of `minutes` anchored at 9:30 on each day: every block but
    the last is proven complete by the later block's existence; the
    last one counts only if its final bar is the block's final minute."""
    out, key, last_c, last_off = [], None, None, None
    for b in bars:
        ts = b[0]
        off = _offset(ts)
        k = (ts.date(), off // minutes)
        if key is not None and k != key:
            out.append(last_c)
        key, last_c, last_off = k, b[4], off
    if key is not None and last_off is not None and last_off % minutes == minutes - 1:
        out.append(last_c)
    return out


def legs_at(series, i_go):
    """Pure. series = the ticker's continuous RTH 1m bars across days;
    i_go = the GO bar's index in it. MACD per timeframe on completed
    closes through the GO bar; n_above / extended None on any warmup."""
    window = series[max(0, i_go - LOOKBACK_1M):i_go + 1]
    out = {}
    for m, name in TIMEFRAMES:
        closes = [b[4] for b in window] if m == 1 else block_closes(window, m)
        got = macd(closes)
        out[name] = None if got is None else {"line": got[0], "signal": got[1], "hist": got[2]}
    lines = [out[name]["line"] if out[name] is not None else None for _, name in TIMEFRAMES]
    if any(v is None for v in lines):
        out["n_above"], out["extended"] = None, None
    else:
        out["n_above"] = sum(1 for v in lines if v > 0)
        out["extended"] = out["n_above"] == len(lines)
    return out


def book_r(day_bars, i_go, entry, stop):
    """The live book's R on the GO risk unit: lifecycle_state's exit,
    else the day's last close (eod_flat)."""
    risk = entry - stop
    if risk <= 0:
        return None
    st = lifecycle_state([b[:5] for b in day_bars], i_go, entry, stop)
    px = st["exit"][2] if st["exit"] is not None else day_bars[-1][4]
    return round((px - entry) / risk, 3)


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
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s", (SOURCE_MARKER,))
            src_done = c.fetchone() is not None
            c.execute("""SELECT e.id, e.ticker, e.trade_date, e.entry_ts, e.entry_px, e.stop_px
                         FROM rs_leader_events e
                         WHERE e.role='leader' AND e.entry_kind='go_pullback'
                           AND e.entry_ts IS NOT NULL AND e.stop_px IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM rsl_macd_events v WHERE v.event_id = e.id)
                         ORDER BY e.ticker, e.trade_date""")
            todo = c.fetchall()
        if not todo:
            if src_done:
                with conn.cursor() as c:
                    c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                              "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
                conn.commit()
            return True
        series, day_idx, cur_tk = [], {}, None
        n = 0
        for eid, tk, d, ets, entry, stop in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[rsl-macd] budget hit; resuming.")
                return False
            if tk != cur_tk:
                with conn.cursor() as c:
                    c.execute("""SELECT ts, trade_date, open, high, low, close FROM mag7_1m_bars
                                 WHERE ticker=%s ORDER BY ts""", (tk,))
                    series, day_idx = [], {}
                    for ts, dd, o, h, l, cl in c.fetchall():
                        i = len(series)
                        series.append((ts.astimezone(et), float(o), float(h), float(l), float(cl)))
                        s, _e = day_idx.get(dd, (i, i))
                        day_idx[dd] = (s, i + 1)
                cur_tk = tk
            span = day_idx.get(d)
            if span is None or span[1] - span[0] < 60:
                continue
            day_bars = series[span[0]:span[1]]
            ets_et = ets.astimezone(et)
            i_day = next((i for i, b in enumerate(day_bars) if b[0] >= ets_et), None)
            if i_day is None:
                continue
            i_go = span[0] + i_day
            entry, stop = float(entry), float(stop)
            legs = legs_at(series, i_go)
            outc = outcomes_at(day_bars, i_day, entry)
            outc["r_close"] = round((outc["close_px"] - entry) / (entry - stop), 3) if entry > stop else None
            outc["r_book"] = book_r(day_bars, i_day, entry, stop)
            outc["half"] = "h1" if d < HALF_SPLIT else "h2"
            with conn.cursor() as c:
                c.execute("""INSERT INTO rsl_macd_events
                             (event_id, ticker, trade_date, macd, n_above, extended, outcomes)
                             VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                          (eid, tk, d, json.dumps(legs), legs["n_above"], legs["extended"],
                           json.dumps(outc)))
            conn.commit()
            n += 1
        log.info("[rsl-macd] graded %d GO entries this pass.", n)
        return False
    finally:
        conn.close()


READOUT_SQL = """
-- by n_above, both halves (±10R cap, NULL-guarded)
SELECT n_above, outcomes->>'half' AS half, count(*) AS n,
       round(avg((outcomes->>'eod_bps')::numeric), 1) AS avg_bps,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (outcomes->>'eod_bps')::numeric)::numeric, 1) AS med_bps,
       round(100.0 * avg(((outcomes->>'eod_bps')::numeric > 0)::int), 1) AS win_pct,
       round(avg(LEAST(GREATEST((outcomes->>'r_close')::numeric, -10), 10)) FILTER (WHERE outcomes->>'r_close' IS NOT NULL), 3) AS avg_r_close,
       round(avg(LEAST(GREATEST((outcomes->>'r_book')::numeric, -10), 10)) FILTER (WHERE outcomes->>'r_book' IS NOT NULL), 3) AS avg_r_book
FROM rsl_macd_events GROUP BY 1, 2 ORDER BY 1, 2;
-- extended vs not, per name
SELECT ticker, extended, count(*) AS n,
       round(avg((outcomes->>'eod_bps')::numeric), 1) AS avg_bps,
       round(100.0 * avg(((outcomes->>'eod_bps')::numeric > 0)::int), 1) AS win_pct
FROM rsl_macd_events WHERE extended IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2;
"""
