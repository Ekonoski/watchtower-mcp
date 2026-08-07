"""
Replay-harness tests: the simulation core must enforce the same house rules
the live loop does — wick rule, entry acceptance, no entry-bar lookahead,
clock rules, two-stop halt — and build_gamma_specs must stay the single
source of truth both engines share.

Standalone:  python3 tests/test_gamma_replay.py    # or: pytest tests/
"""
import datetime as dt
import os
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.gamma_replay import simulate_day, summarize          # noqa: E402
from analysis.paper_trader import build_gamma_specs                # noqa: E402

ET = zoneinfo.ZoneInfo("America/New_York")
DAY = dt.date(2026, 8, 3)


def _bars(seq, start=(9, 45)):
    """seq of (o, h, l, c) → 15m bars with ET end-times from `start`."""
    t = dt.datetime.combine(DAY, dt.time(*start), tzinfo=ET)
    out = []
    for o, h, l, c in seq:
        out.append(dict(end=t, o=o, h=h, l=l, c=c))
        t += dt.timedelta(minutes=15)
    return out


def _fade(trig=100.0, stop=100.15, tgt=98.0):
    return [(DAY, "gamma", "SPY", "short", f"wall_fade_{trig:g}",
             trig, stop, tgt, "armed", "test spec")]


def test_touch_without_reclaim_close_is_not_entry():
    # Wall touched, but every close holds ABOVE it (breakout, no rejection) —
    # the fade never gets its acceptance close, so no trade all day.
    bars = {"SPY": _bars([(100.1, 100.5, 99.9, 100.3)]
                         + [(100.3, 100.9, 100.2, 100.6)] * 5)}
    assert simulate_day(_fade(), bars) == []


def test_touch_then_close_back_through_enters_and_hits_target():
    bars = {"SPY": _bars([
        (99.5, 100.4, 99.4, 100.2),   # touch, but close ABOVE wall — no entry
        (100.2, 100.3, 99.6, 99.7),   # close back under → short entry @ 99.70
        (99.7, 99.8, 97.9, 98.2),     # target 98.00 touched intrabar
    ])}
    trades = simulate_day(_fade(), bars)
    assert len(trades) == 1
    t = trades[0]
    assert t["reason"] == "target" and t["entry_px"] == 99.7 and t["exit_px"] == 98.0
    assert t["r"] > 0


def test_entry_bar_range_never_fills_target():
    # The entry bar's own low pokes the target before the entry exists; the
    # only later bar is EOD. Must exit eod_flat, not credit a phantom target.
    bars = {"SPY": _bars([
        (99.5, 100.4, 99.4, 100.2),
        (100.2, 100.3, 97.9, 99.7),   # entry bar; low 97.9 < tgt 98 — pre-entry
        (99.7, 99.9, 99.5, 99.6),
    ])}
    trades = simulate_day(_fade(), bars)
    assert len(trades) == 1 and trades[0]["reason"] == "eod_flat"


def test_stop_is_close_beyond_not_wick():
    bars = {"SPY": _bars([
        (99.5, 100.4, 99.4, 100.2),
        (100.2, 100.3, 99.6, 99.7),   # short entry
        (99.7, 100.5, 99.6, 100.1),   # wick above stop 100.15, close below — hold
        (100.1, 100.4, 100.0, 100.3),  # CLOSE 100.3 beyond stop — acceptance, out
    ])}
    trades = simulate_day(_fade(), bars)
    assert len(trades) == 1
    assert trades[0]["reason"] == "stop" and trades[0]["exit_px"] == 100.3


def test_no_new_entries_after_1430():
    flat = [(99.4, 99.6, 99.2, 99.3)] * 19          # ends 9:45 .. 14:15
    late = [(99.3, 100.4, 99.2, 99.5),              # ends 14:30 — blocked
            (99.5, 100.4, 99.2, 99.4),              # ends 14:45 — blocked
            (99.4, 99.6, 99.2, 99.3)]
    bars = {"SPY": _bars(flat + late)}
    assert simulate_day(_fade(), bars) == []


def test_two_stops_halt_the_book():
    # Two specs stop out; a third in the same book touches+accepts after — no entry.
    specs = _fade(100.0, 100.15, 98.0) \
        + [(DAY, "gamma", "QQQ", "short", "wall_fade_200", 200.0, 200.3, 196.0,
            "armed", "t"),
           (DAY, "gamma", "IWM", "short", "wall_fade_50", 50.0, 50.08, 49.0,
            "armed", "t")]
    bars = {
        "SPY": _bars([(99.5, 100.1, 99.4, 99.9), (99.9, 100.6, 99.8, 100.5),
                      (100.5, 100.6, 100.4, 100.5), (100.5, 100.6, 100.4, 100.5)]),
        "QQQ": _bars([(199.5, 200.2, 199.0, 199.8), (199.8, 201.0, 199.7, 200.9),
                      (200.9, 201.0, 200.8, 200.9), (200.9, 201.0, 200.8, 200.9)]),
        "IWM": _bars([(49.7, 49.9, 49.6, 49.8), (49.8, 49.9, 49.7, 49.8),
                      (49.8, 50.05, 49.7, 49.9),   # touch + accept AFTER 2 stops
                      (49.9, 50.0, 49.0, 49.2)]),
    }
    trades = simulate_day(specs, bars)
    assert sorted(t["ticker"] for t in trades) == ["QQQ", "SPY"]
    assert all(t["reason"] == "stop" for t in trades)


def test_build_gamma_specs_shared_rules():
    # Load-bearing pinning board below CW → wall fade; decoration board → skip.
    levels = [("SPY", 636.0, 640.0, 630.0, 632.0, 2.1, "pinning"),
              ("IWM", 224.0, 226.0, 220.0, 222.0, 0.3, "pinning")]
    specs, skips = build_gamma_specs(DAY, levels, "armed")
    assert any(s[4] == "wall_fade_640" for s in specs)
    assert skips and skips[0][0] == "IWM" and "below load-bearing" in skips[0][1]


def test_summarize_counts_every_trade():
    trades = [dict(setup="wall_fade_640", r=1.5), dict(setup="wall_fade_500", r=-1.0),
              dict(setup="flip_hold_630", r=0.4)]
    rows = {r[0]: r for r in summarize(trades)}
    assert rows["wall_fade"][1] == 2 and rows["ALL"][1] == 3
    assert abs(rows["ALL"][3] - 0.9) < 1e-9


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
