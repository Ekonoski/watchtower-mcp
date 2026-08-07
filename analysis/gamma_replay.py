"""
Watchtower — gamma-board replay harness (the Tier-1 backtest).

Replays each stored board in gex_levels against that day's completed
15-minute bars (aggregated from minute_bars) and grades every gamma spec the
live engine would have written. Specs come from paper_trader.build_gamma_specs
— the same code path that arms real paper specs — so the replay measures the
playbook, not a reimplementation of it.

The information set is honest by construction: the board for day D is the
latest snapshot computed BEFORE D's 09:30 ET open (in practice the D-1
post-close sweep — the same row the live 7:40 spec-writer reads). Binary days
are skipped exactly as the live binary gate skips them.

Deliberate divergences from the live loop, both conservative:
  - eod_flat exits at the close of the final 15m bar (15:45–16:00) rather
    than live's 15:55 mark-out.
  - entries fill at the trigger bar's close with zero poll lag (live enters
    up to 5 min after the bar completes, at the same bar's close).

Usage:  python3 analysis/gamma_replay.py [START [END]]     # ET dates
"""
import datetime as dt
import os
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ET = zoneinfo.ZoneInfo("America/New_York")
NO_NEW = dt.time(14, 30)   # live loop blocks entries once now >= 14:30 ET


# ── Pure simulation core (no DB — testable, and runnable on exported data) ───

def simulate_day(specs, bars_by_ticker):
    """Walk one day's completed 15m bars through the live-loop rules.

    specs: paper_specs value tuples from build_gamma_specs (armed only).
    bars_by_ticker: {ticker: [bar, ...]} where bar is a dict with keys
        end (ET datetime, bar END), o, h, l, c — regular session, in order.
    Returns a list of trade dicts (book/ticker/setup/entry/exit/reason/r).
    """
    st = []
    for (_d, book, tk, direction, setup, trig, stop, tgt, status, _why) in specs:
        if status != "armed":
            continue
        st.append(dict(book=book, tk=tk, dir=direction, setup=setup,
                       trig=float(trig), stop=float(stop), tgt=float(tgt),
                       touched=False, entry=None, entry_end=None, done=None))
    if not st:
        return []
    ends = sorted({b["end"] for bars in bars_by_ticker.values() for b in bars})
    if not ends:
        return []
    last_end = ends[-1]
    by_end = {(tk, b["end"]): b for tk, bars in bars_by_ticker.items() for b in bars}
    book_stops, trades = {}, []
    for end in ends:
        eod_bar = end == last_end
        for s in st:
            if s["done"]:
                continue
            bar = by_end.get((s["tk"], end))
            if bar is None:
                continue
            h, l, c = bar["h"], bar["l"], bar["c"]
            sign = 1 if s["dir"] == "long" else -1
            if s["entry"] is None:
                if (l <= s["trig"] <= h) or abs(c - s["trig"]) / s["trig"] <= 0.001:
                    s["touched"] = True
                if book_stops.get(s["book"], 0) >= 2 or end.time() >= NO_NEW:
                    if eod_bar:
                        s["done"] = "never_triggered"
                    continue
                if s["touched"] and sign * (c - s["trig"]) > 0:
                    s["entry"], s["entry_end"] = c, end
            else:
                # The entry bar's range is pre-entry price action — stop and
                # target only count on bars ending after the entry bar.
                post = end > s["entry_end"]
                exit_px = reason = None
                if post and sign * (c - s["stop"]) < 0:
                    exit_px, reason = c, "stop"
                elif post and ((s["dir"] == "long" and h >= s["tgt"])
                               or (s["dir"] == "short" and l <= s["tgt"])):
                    exit_px, reason = s["tgt"], "target"
                elif eod_bar:
                    exit_px, reason = c, "eod_flat"
                if exit_px is not None:
                    if reason == "stop":
                        book_stops[s["book"]] = book_stops.get(s["book"], 0) + 1
                    r_dist = abs(s["trig"] - s["stop"]) or 0.01
                    trades.append(dict(book=s["book"], ticker=s["tk"],
                                       direction=s["dir"], setup=s["setup"],
                                       entered=s["entry_end"], entry_px=s["entry"],
                                       exited=end, exit_px=exit_px, reason=reason,
                                       r=round(sign * (exit_px - s["entry"]) / r_dist, 2)))
                    s["done"] = reason
    return trades


def summarize(all_trades):
    """Per-setup-family and overall stats. Returns list of (label, n, wins,
    total_r, avg_r) rows — every trade counted, nothing dropped."""
    rows = []
    fams = sorted({t["setup"].rsplit("_", 1)[0] for t in all_trades})
    for fam in fams + ["ALL"]:
        sel = [t for t in all_trades
               if fam == "ALL" or t["setup"].rsplit("_", 1)[0] == fam]
        if not sel:
            continue
        wins = sum(1 for t in sel if t["r"] > 0)
        tot = round(sum(t["r"] for t in sel), 2)
        rows.append((fam, len(sel), wins, tot, round(tot / len(sel), 2)))
    return rows


# ── DB plumbing (server-side) ────────────────────────────────────────────────

def _fetch_day(conn, day, venue, binary_events):
    """Returns (board_rows, board_stamp, binary_day, bars_by_ticker)."""
    open_et = dt.datetime.combine(day, dt.time(9, 30), tzinfo=ET)
    close_et = dt.datetime.combine(day, dt.time(16, 0), tzinfo=ET)
    with conn.cursor() as c:
        c.execute("""SELECT DISTINCT ON (ticker) ticker, spot, call_wall, put_wall,
                            gamma_flip, net_gex, regime, computed_at
                     FROM gex_levels
                     WHERE ticker = ANY(%s) AND computed_at < %s
                       AND computed_at > %s - interval '4 days'
                     ORDER BY ticker, computed_at DESC""", (venue, open_et, open_et))
        rows = c.fetchall()
        board = [r[:7] for r in rows]
        stamp = max((r[7] for r in rows), default=None)
        c.execute("""SELECT event FROM economic_calendar
                     WHERE country='US' AND event_date=%s AND impact='High'""", (day,))
        highs = [r[0] for r in c.fetchall()]
        binary = any(b.lower() in e.lower() for e in highs for b in binary_events)
        bars = {}
        for tk in venue:
            c.execute("""SELECT to_timestamp(floor(extract(epoch FROM ts)/900)*900)
                                + interval '15 minutes' AS bar_end,
                                (array_agg(open ORDER BY ts))[1], max(high), min(low),
                                (array_agg(close ORDER BY ts DESC))[1]
                         FROM minute_bars
                         WHERE ticker=%s AND ts >= %s AND ts < %s
                         GROUP BY 1 ORDER BY 1""", (tk, open_et, close_et))
            bars[tk] = [dict(end=e.astimezone(ET), o=float(o), h=float(h),
                             l=float(l), c=float(cl))
                        for e, o, h, l, cl in c.fetchall()]
    return board, stamp, binary, bars


def replay(start, end):
    from screen.reversal_screen import _conn as get_db_connection
    from analysis.paper_trader import build_gamma_specs, VENUE, BINARY_EVENTS
    conn = get_db_connection()
    all_trades, skipped = [], []
    try:
        day = start
        while day <= end:
            if day.weekday() >= 5:
                day += dt.timedelta(days=1)
                continue
            board, stamp, binary, bars = _fetch_day(conn, day, list(VENUE), BINARY_EVENTS)
            close_et = dt.datetime.combine(day, dt.time(16, 0), tzinfo=ET)
            if not board:
                skipped.append((day, "no board before open"))
            elif binary:
                skipped.append((day, "binary day — gate"))
            elif not any(bars.values()):
                skipped.append((day, "no minute bars"))
            elif max(b["end"] for bs in bars.values() for b in bs) < close_et:
                # A half-archived session would grade eod_flat against a
                # mid-afternoon bar — not a smaller sample, a wrong one.
                skipped.append((day, "partial session data — archive mid-backfill"))
            else:
                specs, spec_skips = build_gamma_specs(day, board, "armed")
                trades = simulate_day(specs, bars)
                for t in trades:
                    t["date"] = day
                all_trades += trades
                print(f"{day}  board {stamp:%m-%d %H:%M ET} | "
                      f"{len(specs)} spec(s), {len(trades)} trade(s)"
                      + (f" | no-spec: {'; '.join(f'{t} {w}' for t, w in spec_skips)}"
                         if spec_skips else ""))
                for t in trades:
                    print(f"    {t['setup']:<22} {t['ticker']} {t['direction']:<5} "
                          f"in {t['entry_px']:.2f} @{t['entered']:%H:%M} → "
                          f"out {t['exit_px']:.2f} @{t['exited']:%H:%M}  "
                          f"{t['reason']:<8} {t['r']:+.2f}R")
            day += dt.timedelta(days=1)
    finally:
        conn.close()
    print(f"\nskipped days ({len(skipped)}):")
    for d, why in skipped:
        print(f"    {d}  {why}")
    print("\nsetup                    n   wins   totR    avgR")
    for fam, n, wins, tot, avg in summarize(all_trades):
        print(f"{fam:<22} {n:>3} {wins:>6} {tot:>+7.2f} {avg:>+7.2f}")
    return all_trades


if __name__ == "__main__":
    a = sys.argv[1:]
    end_d = dt.date.fromisoformat(a[1]) if len(a) > 1 else dt.date.today()
    start_d = dt.date.fromisoformat(a[0]) if a else end_d - dt.timedelta(days=45)
    replay(start_d, end_d)
