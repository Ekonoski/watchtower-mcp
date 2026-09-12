"""
The SELECTION cut on the two-test engine (2026-09-12 — Eric, on the
first readout: "in order for it to survive we have to pick the stocks
that are moving in the right direction, based on the one hour, the
fifteen, the five, the one minute, as well as … picking the right
sector that's also showing life … then picking the leaders within
those sectors. Are you doing that, or are you just grabbing names and
testing?" — the honest answer was: grabbing names, with one leader cut).

This stamps every TRIGGERED twotest event with the three selection
legs he named, read at the trigger with no lookahead, so the engine
grades conditioned on them:

  alignment   the Scanner v1.5 trend gate (tapeentry_study.trend_series,
              one definition) on FOUR timeframes at the trigger bar —
              1m, 5m, 15m, 60m — each series continuous across days per
              ticker (an RTH chart), the 5m/15m/60m read on the last
              COMPLETED block before the trigger minute (the forming
              block is a forming bar). trend_* in {-1, 0, +1};
              n_aligned = how many of the four point the trade's way;
              aligned = all four.
  sector      the name's sector (tickers.sector) and its breadth-RS
              read at the PRIOR close (sector_rs_daily, the freshest
              row strictly before the trade date): rank_1m of the
              sectors that day, rs_1w; state = inflow (rank ≤ 3) /
              neutral / outflow (rank ≥ 9), turning = rs_1w > 0 — the
              sector study's own cells (its one live cell was outflow
              on the month AND turning up on the week). SPY/QQQ/IWM
              carry no sector: a hole, never neutral.
  leader      rs_leader_events' 9:45 leader that day == this name.

READOUT: struct_5c and eod bps by half × aligned × sector state ×
leader, the same bar as the engine (positive in BOTH halves; cells
under 40 render small-n). Stated: the four-timeframe gate needs 21
completed bars per timeframe, so the 60m leg is unreadable before
~11:00 on a fresh series only at the very start of the record (it is
continuous, so warmup is a one-time cost) — a trend of 0 is "gate not
on", never "unknown"; unknown (warmup) is NULL.

Writes ONLY twotest_select (one row per triggered event, keyed by the
event id). Marker twotest_select_v1.
"""
import datetime as dt
import logging
import time

from analysis.tapeentry_study import ema_series, resample5, trend_series
from analysis.twotest_study import MAG7, TICKERS, _bars_table

log = logging.getLogger("watchtower.twotest_select")

COMPLETE_MARKER = "twotest_select_v1"
FRAMES = (1, 5, 15, 60)
# The tickers table maps the index ETFs to "Financial Services" (a
# vendor default, not a sector). An ETF has no sector read: a hole.
ETFS = frozenset({"SPY", "QQQ", "IWM"})
BUDGET_S = 25 * 60
INFLOW_RANK = 3
OUTFLOW_RANK = 9


# ── pure cores ───────────────────────────────────────────────────────

def resample_n(bars, n):
    """Fixed-anchor n-minute resample of RTH 1m bars, bucketed by
    (date, minutes-since-9:30 // n) — the resample5 definition with the
    bucket size as a parameter (tests pin resample_n(bars, 5) ==
    resample5(bars)). Returns (bars_n, last1m)."""
    out, last1m = [], []
    cur_key = None
    o = h = l = c = None
    for i, (ts, bo, bh, bl, bc) in enumerate(bars):
        mins = (ts.hour - 9) * 60 + ts.minute - 30
        key = (ts.date(), mins // n)
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


def frame_trends(bars1):
    """Pure. For each frame: (trend list, last1m list) on the continuous
    series. The 1m frame's last1m is the identity."""
    out = {}
    for n in FRAMES:
        if n == 1:
            b, last = bars1, list(range(len(bars1)))
        else:
            b, last = resample_n(bars1, n)
        closes = [x[4] for x in b]
        e8, e21 = ema_series(closes, 8), ema_series(closes, 21)
        tr = trend_series(closes, e8, e21)
        out[n] = (tr, last, len(b))
    return out


def trend_at(frame, i_trig):
    """Pure. The frame's trend on the last block COMPLETED before the
    1m index i_trig (the block holding i_trig is forming). None inside
    the gate's warmup (unknown is never 0)."""
    tr, last, n = frame
    # last[k] = the 1m index of block k's final bar; completed before
    # i_trig means last[k] < i_trig.
    k = None
    lo, hi = 0, n - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if last[mid] < i_trig:
            k = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if k is None or k < 21:
        return None
    return tr[k]


def sector_state(rank_1m, rs_1w):
    """Pure. The sector study's cells."""
    if rank_1m is None:
        return None, None
    st = "inflow" if rank_1m <= INFLOW_RANK else ("outflow" if rank_1m >= OUTFLOW_RANK else "neutral")
    return st, (rs_1w is not None and rs_1w > 0)


# ── the pass ─────────────────────────────────────────────────────────

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
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name='twotest_v1'")
            if not c.fetchone():
                log.info("[twotest_select] waiting for twotest_v1.")
                return False
            c.execute("SELECT ticker, sector FROM tickers WHERE ticker = ANY(%s)", (list(TICKERS),))
            sectors = {t: s for t, s in c.fetchall()}
            c.execute("SELECT trade_date, ticker FROM rs_leader_events WHERE role='leader' AND entry_kind='go_pullback'")
            leaders = {(d, t) for d, t in c.fetchall()}
            c.execute("SELECT trade_date, sector, rank_1m, rs_1w FROM sector_rs_daily ORDER BY trade_date")
            srs = {}
            for d, s, rk, rw in c.fetchall():
                srs.setdefault(s, []).append((d, int(rk) if rk is not None else None,
                                              float(rw) if rw is not None else None))
        for tk in TICKERS:
            if time.time() - t0 > BUDGET_S:
                log.info("[twotest_select] budget hit; resuming next pass.")
                return False
            with conn.cursor() as c:
                c.execute("""SELECT e.id, e.trade_date, e.direction, e.trigger_ts
                             FROM twotest_events e
                             WHERE e.ticker=%s AND e.status='triggered'
                               AND NOT EXISTS (SELECT 1 FROM twotest_select s WHERE s.event_id=e.id)
                             ORDER BY e.trigger_ts""", (tk,))
                todo = c.fetchall()
            if not todo:
                continue
            with conn.cursor() as c:
                c.execute(f"SELECT ts, open, high, low, close FROM {_bars_table(tk)} WHERE ticker=%s "
                          f"AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL "
                          f"ORDER BY ts", (tk,))
                raw = c.fetchall()
            bars1 = [(ts.astimezone(et), float(o), float(h), float(l), float(cl))
                     for ts, o, h, l, cl in raw
                     if dt.time(9, 30) <= ts.astimezone(et).time() <= dt.time(15, 59)]
            idx = {b[0]: i for i, b in enumerate(bars1)}
            frames = frame_trends(bars1)
            sec = None if tk in ETFS else sectors.get(tk)
            rows = []
            for eid, d, direction, trig_ts in todo:
                i = idx.get(trig_ts.astimezone(et))
                if i is None:
                    continue                                   # bar not on record: hole
                sign = 1 if direction == "long" else -1
                tr = {n: trend_at(frames[n], i) for n in FRAMES}
                known = [v for v in tr.values() if v is not None]
                n_al = sum(1 for v in known if v == sign)
                aligned = (len(known) == 4 and n_al == 4)
                rank = rsw = None
                if sec and sec in srs:
                    prior = [r for r in srs[sec] if r[0] < d]
                    if prior:
                        _, rank, rsw = prior[-1]
                st, turning = sector_state(rank, rsw)
                rows.append((eid, tr[1], tr[5], tr[15], tr[60], n_al if len(known) == 4 else None,
                             aligned if len(known) == 4 else None, sec, rank, rsw, st, turning,
                             (d, tk) in leaders))
            with conn.cursor() as c:
                c.executemany("""INSERT INTO twotest_select
                    (event_id, trend_1m, trend_5m, trend_15m, trend_60m, n_aligned, aligned,
                     sector, rank_1m, rs_1w, sector_state, turning, leader)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", rows)
            conn.commit()
            log.info("[twotest_select] %s: +%d", tk, len(rows))
        with conn.cursor() as c:
            c.execute("""SELECT count(*) FROM twotest_events e WHERE e.status='triggered'
                         AND NOT EXISTS (SELECT 1 FROM twotest_select s WHERE s.event_id=e.id)""")
            left = c.fetchone()[0]
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) VALUES (%s, CURRENT_DATE) "
                      "ON CONFLICT DO NOTHING", (COMPLETE_MARKER,))
        conn.commit()
        log.info("[twotest_select] complete — marker %s (%d events unstampable, holes).", COMPLETE_MARKER, left)
        return True
    finally:
        conn.close()


READOUT_SQL = """
-- the engine under the selection legs, both halves, NULL-guarded
SELECT CASE WHEN e.trade_date < '2025-09-01' THEN 'h1' ELSE 'h2' END half,
       s.aligned, s.sector_state, s.turning, s.leader, count(*) n,
       round(avg(e.eod_bps),1) eod_bps,
       round(avg((e.stops->'struct_5c'->>'bps')::numeric),1) s5c_bps,
       round(100.0*count(*) FILTER (WHERE (e.stops->'struct_5c'->>'bps')::numeric > 0)/count(*),0) pct_pos
FROM twotest_events e JOIN twotest_select s ON s.event_id=e.id
WHERE e.status='triggered' AND e.direction='long'
GROUP BY 1,2,3,4,5 ORDER BY 2,3,4,5,1;
"""
