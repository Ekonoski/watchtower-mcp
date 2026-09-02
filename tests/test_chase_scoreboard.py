"""2026-09-02 evening pins:
  1. chase_study.simulate_fills: f=0 is the graded entry itself; a
     premium fill is granted only if a bar in the window traded there
     (else no_fill, a skipped trade not a zero); the stop is unchanged
     and R is measured against the WIDER risk the chaser took; the
     lifecycle is the book's own (imported, never copied).
  2. desk_events.format_scoreboard: worst book first, the gamma
     head-to-head line, disagreements or the zero-is-data line.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_chase_fills_semantics():
    from analysis import chase_study as cs
    t0 = dt.datetime(2026, 9, 2, 9, 50)

    def bars(specs):
        return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]
    go_close, stop = 100.0, 99.0          # risk 1.0
    # GO bar, then 3 bars: highs 100.2, 100.45, 100.9 (so f=.10 fills at
    # bar 1, f=.25 at bar 2, f=.50 at bar 3, f=1.0 never inside the window), then a run
    # to 104 and a 5m close back at 103.
    specs = [(100, 100.1, 99.8, 100.0), (100, 100.2, 99.9, 100.1),
             (100.1, 100.45, 100.0, 100.4), (100.4, 100.9, 100.3, 100.8)]
    specs += [(101, 104.0, 100.9, 103.8)] * 6
    specs += [(103.5, 103.6, 102.9, 103.0)] * 5
    b = bars(specs)
    out = cs.simulate_fills(b, 0, go_close, stop)
    assert out["0.00"]["delay_min"] == 0 and out["0.00"]["fill"] == 100.0
    assert out["0.10"]["delay_min"] == 1 and out["0.25"]["delay_min"] == 2
    assert out["0.50"]["delay_min"] == 3
    assert out["1.00"]["out"] == "no_fill"
    # wider risk -> smaller R for the same exit
    assert out["0.50"]["r"] < out["0.00"]["r"]
    src = inspect.getsource(cs)
    assert "from analysis.rs_leader_book import lifecycle_state" in src
    assert "INSERT INTO chase_events" in src and "paper_trades" not in src


def test_scoreboard_format():
    from alerts.desk_events import format_scoreboard
    rows = [("swing", 15, 2, 13, -12.77), ("gamma", 9, 4, 5, -0.71),
            ("gamma_iday", 7, 6, 1, 3.62), ("rs_leader", 2, 2, 0, 17.36)]
    msg = format_scoreboard(rows, [], dt.date(2026, 9, 2))
    lines = msg.splitlines()
    assert lines[1].startswith("swing")                 # worst first
    assert "gamma_iday" in msg and "Morning board vs live board" in msg
    assert "zero is data" in msg
    msg2 = format_scoreboard(rows, [("SPY", "stack_fade_766.5", "gamma", "gamma_iday")],
                             dt.date(2026, 9, 2))
    assert "disagreed on SPY" in msg2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
