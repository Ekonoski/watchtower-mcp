"""
The RS-leader morning study (2026-09-01 — pre-registered the night
after the QQQ chop beating; Eric: "yes run the RS-leader study
tonight"). The TSLA trade, graded:

  leader      at 9:45, rank the mag 7 by return-from-open minus QQQ's
              return-from-open (QQQ from the stored 15m record). The
              leader qualifies at rank 1 with RS >= +0.4%; the laggard
              short mirror (<= -0.4%) is recorded for the record.
  entry       after 9:45 and before 11:00, the FIRST 1m pullback that
              touches the 1m 8 or 21 EMA from the trend side and
              CLOSES back beyond it (the Scanner v1.6 GO definition,
              wick rule), entered at that 1m close. Stop under the
              pullback bar's extreme (0.05% buffer).
  outcomes    bracket sim on the 1m tape: 2R target vs stop on
              first-touch ORDER semantics (a resting bracket fills on
              touch — this is an order simulation, stated as such;
              same-bar both-touch counts as STOPPED, conservative).
              Plus R at noon, R at close, MFE/MAE in R.
  baselines   (a) the same entry mechanics on the MID-PACK name (rank
              4) — does leadership carry the edge, or any pullback?
              (b) the leader bought at 9:45 with NO pullback — does
              the entry mechanic add anything over owning the leader?
  bar         positive expectancy, sign-consistent across >= 5 of 7
              names, survives a year-over-year split. Era depth is
              limited (2 years of 1m) — cross-name replication is
              this study's robustness leg, stated wherever numbers
              surface.

Writes ONLY rs_leader_events; resumes by processed trade_date;
marker rsleader_study_v1 once the bars backfill marker exists and all
stored days are graded.
"""
import datetime as dt
import logging
import time

log = logging.getLogger("watchtower.rsleader_study")

COMPLETE_MARKER = "rsleader_study_v1"
BARS_MARKER = "rsleader_bars_v1"
TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
RS_MIN = 0.4                  # percent vs QQQ at 9:45
ENTRY_CUTOFF = dt.time(11, 0)
MEASURE = dt.time(9, 45)
STOP_BUFF = 0.0005
BUDGET_S = 20 * 60


def ema(vals, n):
    out, k = [], 2 / (n + 1)
    e = vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rs_rank(rets: dict, qqq_ret: float):
    """Pure: {ticker: pct_return_from_open} -> (leader, laggard,
    midpack, rs_dict). leader/laggard are None unless they clear the
    +-RS_MIN bar vs QQQ."""
    rs = {t: r - qqq_ret for t, r in rets.items()}
    ordered = sorted(rs, key=lambda t: rs[t], reverse=True)
    leader = ordered[0] if rs[ordered[0]] >= RS_MIN else None
    laggard = ordered[-1] if rs[ordered[-1]] <= -RS_MIN else None
    midpack = ordered[len(ordered) // 2]
    return leader, laggard, midpack, rs


def find_go_entry(bars, e8, e21, start_i, cutoff_i, direction):
    """Pure: first 1m bar in [start_i, cutoff_i) that touches the 8 or
    21 EMA from the trend side and CLOSES back beyond it (wick rule).
    bars = [(ts, o, h, l, c), ...]. Returns (i, entry_px, stop_px) or
    None."""
    for i in range(start_i, min(cutoff_i, len(bars))):
        ts, o, h, l, c = bars[i]
        if direction == "long":
            if (l <= e8[i] and c > e8[i]) or (l <= e21[i] and c > e21[i]):
                return i, c, l * (1 - STOP_BUFF)
        else:
            if (h >= e8[i] and c < e8[i]) or (h >= e21[i] and c < e21[i]):
                return i, c, h * (1 + STOP_BUFF)
    return None


def sim_bracket(bars, i_entry, entry, stop, direction):
    """Pure: first-touch order sim from the bar AFTER entry to EOD.
    2R target vs stop; same-bar both-touch = STOPPED (conservative).
    Returns (outcome, r_first, r_noon, r_close, mfe_r, mae_r)."""
    ru = abs(entry - stop)
    if ru <= 0:
        return ("no_entry", None, None, None, None, None)
    sign = 1.0 if direction == "long" else -1.0
    tgt = entry + sign * 2 * ru
    mfe = 0.0
    mae = 0.0
    r_noon = None
    outcome, r_first = "eod_flat", None
    for ts, o, h, l, c in bars[i_entry + 1:]:
        fav = ((h - entry) if direction == "long" else (entry - l)) / ru
        adv = ((l - entry) if direction == "long" else (entry - h)) / ru
        mfe = max(mfe, fav)
        mae = min(mae, adv)
        hit_stop = l <= stop if direction == "long" else h >= stop
        hit_tgt = h >= tgt if direction == "long" else l <= tgt
        if r_noon is None and ts.time() >= dt.time(12, 0):
            r_noon = sign * (c - entry) / ru
        if outcome == "eod_flat":
            if hit_stop:
                outcome, r_first = "stopped", -1.0
            elif hit_tgt:
                outcome, r_first = "target2", 2.0
    r_close = sign * (bars[-1][4] - entry) / ru
    if r_noon is None:
        r_noon = r_close
    if r_first is None:
        r_first = r_close
    return (outcome, round(r_first, 2), round(r_noon, 2), round(r_close, 2),
            round(mfe, 2), round(mae, 2))


def _grade_day(conn, d):
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    with conn.cursor() as c:
        c.execute("""SELECT open, close FROM index_intraday_bars
                     WHERE ticker='QQQ' AND trade_date=%s
                       AND (ts AT TIME ZONE 'America/New_York')::time='09:30'
                  """, (d,))
        r = c.fetchone()
    if not r:
        return 0                      # QQQ reference hole; day skipped, logged by caller
    qqq_ret = (float(r[1]) / float(r[0]) - 1) * 100

    day = {}
    with conn.cursor() as c:
        c.execute("""SELECT ticker, ts, open, high, low, close
                     FROM mag7_1m_bars WHERE trade_date=%s
                     ORDER BY ticker, ts""", (d,))
        for tk, ts, o, h, l, cl in c.fetchall():
            day.setdefault(tk, []).append(
                (ts.astimezone(et), float(o), float(h), float(l), float(cl)))
    rets = {}
    for tk, bars in day.items():
        if len(bars) < 120:
            continue
        o930 = bars[0][1]
        p945 = None
        for ts, o, h, l, cl in bars:
            if ts.time() < MEASURE:
                p945 = cl
            else:
                break
        if p945 is not None:
            rets[tk] = (p945 / o930 - 1) * 100
    if len(rets) < 5:
        return 0
    leader, laggard, midpack, rs = rs_rank(rets, qqq_ret)

    rows = []
    for tk, role, direction in ((leader, "leader", "long"),
                                (laggard, "laggard", "short"),
                                (midpack, "midpack_baseline", "long")):
        if tk is None:
            continue
        bars = day[tk]
        closes = [b[4] for b in bars]
        e8, e21 = ema(closes, 8), ema(closes, 21)
        i945 = next((i for i, b in enumerate(bars)
                     if b[0].time() >= MEASURE), None)
        icut = next((i for i, b in enumerate(bars)
                     if b[0].time() >= ENTRY_CUTOFF), len(bars))
        if i945 is None:
            continue
        got = find_go_entry(bars, e8, e21, i945, icut, direction)
        if got is None:
            rows.append((d, tk, role, direction, round(rs[tk], 2),
                         "go_pullback", None, None, None,
                         "no_entry", None, None, None, None, None))
        else:
            i, entry, stop = got
            out = sim_bracket(bars, i, entry, stop, direction)
            rows.append((d, tk, role, direction, round(rs[tk], 2),
                         "go_pullback", bars[i][0], round(entry, 4),
                         round(stop, 4), *out))
        if role == "leader":
            i = i945
            entry = bars[i][4]
            stop = min(b[3] for b in bars[:i + 1]) * (1 - STOP_BUFF)
            out = sim_bracket(bars, i, entry, stop, "long")
            rows.append((d, tk, role, "long", round(rs[tk], 2),
                         "no_pullback_945", bars[i][0], round(entry, 4),
                         round(stop, 4), *out))
    if rows:
        with conn.cursor() as c:
            c.executemany("""INSERT INTO rs_leader_events
                (trade_date, ticker, role, direction, rs_945_pct,
                 entry_kind, entry_ts, entry_px, stop_px, outcome,
                 r_first, r_noon, r_close, mfe_r, mae_r)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""", rows)
        conn.commit()
    return len(rows)


def run() -> bool:
    from screen.reversal_screen import _conn
    conn = _conn()
    t0 = time.time()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (BARS_MARKER,))
            bars_done = c.fetchone() is not None
            c.execute("""SELECT DISTINCT b.trade_date FROM mag7_1m_bars b
                         WHERE NOT EXISTS (SELECT 1 FROM rs_leader_events e
                                           WHERE e.trade_date = b.trade_date)
                         ORDER BY b.trade_date""")
            todo = [r[0] for r in c.fetchall()]
        n = 0
        for d in todo:
            if time.time() - t0 > BUDGET_S:
                log.info("[rsleader] budget hit at %s; resuming.", d)
                return False
            n += _grade_day(conn, d)
        log.info("[rsleader] graded %d rows across %d day(s).", n, len(todo))
        if bars_done and not todo:
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                    (COMPLETE_MARKER,))
            conn.commit()
            log.info("[rsleader] complete — marker %s.", COMPLETE_MARKER)
            return True
        return not todo
    finally:
        conn.close()
