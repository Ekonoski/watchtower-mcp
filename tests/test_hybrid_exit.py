"""The hybrid-exit sim, pinned (2026-08-31).

  1. be_1r: after a 1m high touches entry+1R the stop is breakeven —
     a later 5m CLOSE below entry exits ~flat instead of -1R.
  2. trail_1r_5mlow ratchets only AFTER arming and never down; the
     just-completed bar's low never stops itself.
  3. Wick rule holds: a wick through the struct level exits nothing;
     the disaster cap exits on TOUCH.
  4. tgt2 same-bar both-touch = stopped (conservative).
  5. Writes only hybridexit_events — by signature.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.hybrid_exit_study import sim_hybrid  # noqa: E402


def _bars(specs):
    """specs: [(o,h,l,c)] -> 1m bars from 10:00."""
    t0 = dt.datetime(2026, 9, 1, 10, 0)
    return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]


def _e21(bars, val):
    """Every 5th minute (index 4, 9, ...) completes a 5m bar."""
    return {i: val for i in range(len(bars)) if i % 5 == 4}


def test_breakeven_after_1r():
    entry, struct = 100.0, 99.0
    specs = [(100, 101.1, 99.9, 101.0)] * 5      # +1R touched, armed
    specs += [(101, 101, 99.8, 99.9)] * 5        # 5m closes below entry
    bars = _bars(specs)
    out = sim_hybrid(bars, 0, entry, struct, _e21(bars, 90.0))
    assert out["be_1r"]["out"] == "stopped"
    assert abs(out["be_1r"]["r"]) < 0.15         # ~flat, not -1R
    assert out["fixed"]["out"] == "eod"          # struct never closed under


def test_trail_ratchets_up_only_after_arming():
    entry, struct = 100.0, 99.0
    specs = [(100, 101.2, 100.4, 101.0)] * 5     # armed; bar low 100.4
    specs += [(101, 102.2, 101.4, 102.0)] * 5    # ratchet to 101.4
    specs += [(102, 102, 100.9, 101.0)] * 5      # 5m close 101.0 < 101.4
    bars = _bars(specs)
    out = sim_hybrid(bars, 0, entry, struct, _e21(bars, 90.0))
    assert out["trail_1r_5mlow"]["out"] == "stopped"
    assert out["trail_1r_5mlow"]["exit_px"] == 101.0   # locked in ~+1R
    assert out["trail_1r_5mlow"]["r"] > 0.9
    assert out["fixed"]["out"] == "eod"                # baseline still holds


def test_wick_rule_and_disaster_touch():
    entry, struct = 100.0, 99.5
    specs = [(100, 100.2, 99.3, 100.1)] * 5      # wick through struct, closes above
    bars = _bars(specs)
    out = sim_hybrid(bars, 0, entry, struct, _e21(bars, 90.0))
    assert out["fixed"]["out"] == "eod"          # a wick is not a close
    specs2 = [(100, 100.1, 98.9, 100.0)] * 5     # touches -1.1% -> disaster
    bars2 = _bars(specs2)
    out2 = sim_hybrid(bars2, 0, entry, struct, _e21(bars2, 90.0))
    assert out2["fixed"]["out"] == "disaster"
    assert out2["fixed"]["exit_px"] == 99.0      # the cap price, on touch


def test_tgt2_same_bar_is_stopped():
    entry, struct = 100.0, 99.0
    specs = [(100, 102.5, 98.9, 100.0)]          # hits 2R AND stop same bar
    bars = _bars(specs)
    out = sim_hybrid(bars, 0, entry, struct, {})
    assert out["tgt2"]["out"] == "stopped" and out["tgt2"]["r"] == -1.0


def test_writes_only_its_own_table():
    from analysis import hybrid_exit_study
    src = inspect.getsource(hybrid_exit_study)
    assert "INSERT INTO hybridexit_events" in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
