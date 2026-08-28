"""
Mega-cap gamma replay (2026-08-28, Eric: "we have meaningful netGEX on
several other names. will the system also trade those?" → "run the
replay tonight and build it if it passes").

Grades the EXACT index gamma playbook — build_gamma_specs +
gamma_replay.simulate_day, the same code paths the live books and the
Tier-1 harness use — on the seven mega-cap boards (DRIFT_TICKERS minus
the indexes), over every day since their nightly boards began
(2026-07-15, ~33 board days). Nothing is reimplemented; a rule change
would re-grade here identically.

Bars: minute_bars holds no mega-cap history, so each ticker's 15m bars
are research-fetched once from Polygon (reconstruction-is-not-tape
governs LIVE grading; a backtest fetching history is the legitimate
case, same as the defense and day-bias studies). Binary days skip via
the same gate. Results land in gamma_mega_replay; the pass/fail read
happens in review, not in code — this job measures, the humans decide.

One-shot: marker gamma_mega_replay_v1 retires it.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.gamma_mega_replay")

COMPLETE_MARKER = "gamma_mega_replay_v1"
START = dt.date(2026, 7, 15)


def _bars_by_day(ticker, start):
    """One research fetch per ticker → {date: [bar dicts]} of RTH 15m
    bars keyed the way simulate_day wants them (bar END timestamps)."""
    from analysis.paper_trader import ET
    from analysis.polygon_data import fetch_recent_bars
    days_back = (dt.date.today() - start).days + 3
    raw = fetch_recent_bars(ticker, days=days_back, multiplier=15,
                            timespan="minute")
    out = {}
    for b in raw:
        ts = b.get("timestamp")
        if ts is None:
            continue
        t = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).astimezone(ET)
        if not (dt.time(9, 30) <= t.time() <= dt.time(15, 45)):
            continue
        end = t + dt.timedelta(minutes=15)
        out.setdefault(t.date(), []).append(
            dict(end=end, o=float(b["open"]), h=float(b["high"]),
                 l=float(b["low"]), c=float(b["close"])))
    return out


def run() -> bool:
    from analysis.gamma_replay import _fetch_day, simulate_day
    from analysis.gex import MEGACAPS
    from analysis.paper_trader import BINARY_EVENTS, build_gamma_specs
    from screen.reversal_screen import _conn

    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                return True

        venue = list(MEGACAPS)
        bars_all = {tk: _bars_by_day(tk, START) for tk in venue}
        if not any(bars_all.values()):
            log.warning("[mega-replay] no bars fetched — retry next boot")
            return False

        day, end_day = START, dt.date.today() - dt.timedelta(days=1)
        n_days = n_trades = 0
        while day <= end_day:
            if day.weekday() >= 5:
                day += dt.timedelta(days=1)
                continue
            # Board query is the harness's own (bars arg ignored here —
            # minute_bars has no mega coverage; ours come from the fetch).
            board, stamp, binary, _ = _fetch_day(conn, day, venue,
                                                 BINARY_EVENTS)
            bars = {tk: bars_all[tk].get(day, []) for tk in venue}
            if not board or binary or not any(bars.values()):
                day += dt.timedelta(days=1)
                continue
            # Partial-session guard, same spirit as the harness.
            last_end = max((b["end"] for bs in bars.values() for b in bs),
                           default=None)
            if last_end is None or last_end.time() < dt.time(15, 55):
                day += dt.timedelta(days=1)
                continue
            specs, _skips = build_gamma_specs(day, board, "armed")
            trades = simulate_day(specs, bars)
            with conn.cursor() as c:
                for t in trades:
                    c.execute("""INSERT INTO gamma_mega_replay
                        (trade_date, ticker, setup, direction, entry_px,
                         exit_px, entered_et, exited_et, reason, r)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (day, t["ticker"], t["setup"], t["direction"],
                         t["entry_px"], t["exit_px"], t["entered"],
                         t["exited"], t["reason"], t["r"]))
            conn.commit()
            n_days += 1
            n_trades += len(trades)
            day += dt.timedelta(days=1)

        with conn.cursor() as c:
            c.execute("INSERT INTO scheduler_job_claims (job_name, run_date) "
                      "VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING",
                      (COMPLETE_MARKER,))
        conn.commit()
        log.info("[mega-replay] complete — %d day(s), %d trade(s) graded.",
                 n_days, n_trades)
        return True
    finally:
        conn.close()
