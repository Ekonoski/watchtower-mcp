"""The Tape Bot retest-machine study, pinned (2026-08-29).

  1. The machine is the Pine v2.2 port: break -> forced wait ->
     touch -> RETEST only on a close that HOLDS the level (a wick
     through is not proof), HELD after the holding bars, timeout.
  2. A touch that closes back through the level is a refusal
     (RETEST FAIL) and produces NO signal.
  3. Nearest-level selection and the 1% search band, as the script.
  4. Writes only tapebot_retest_events — by signature.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.tapebot_retest_study import (new_state,  # noqa: E402
                                           step)

LV = [("PDH", 100.0), ("PDL", 95.0)]


def _bar(c, h=None, l=None):
    return (c, h if h is not None else c + 0.05,
            l if l is not None else c - 0.05, c)


def test_break_wait_retest_bull_fires():
    s = new_state()
    assert step(s, _bar(100.5), 99.5, LV) == []      # BROKE UP arms
    assert s["st"] == "BROKE UP" and s["lvl"] == 100.0
    assert step(s, _bar(100.6), 100.5, LV) == []     # forced wait
    assert s["st"] == "WAIT RETEST UP"
    # The retest: touches the level, CLOSES above it.
    fresh = step(s, (100.3, 100.4, 100.05, 100.3), 100.6, LV)
    assert fresh == ["RETEST BULL"]


def test_wick_through_is_not_proof():
    s = new_state()
    step(s, _bar(100.5), 99.5, LV)
    step(s, _bar(100.6), 100.5, LV)
    # Touch arrives but the close is back BELOW the level: NO bull
    # signal fires — the machine flips to a fresh BROKE DOWN (the
    # crossing close re-arms it the other way, Pine order pinned
    # above). The wick refusal property: a touch without a holding
    # close never produces RETEST BULL.
    fresh = step(s, (99.9, 100.4, 99.8, 99.9), 100.6, LV)
    assert fresh == [] and s["st"] == "BROKE DOWN"


def test_held_needs_the_holding_bars():
    s = new_state()
    step(s, _bar(100.5), 99.5, LV)                   # age 0
    step(s, _bar(100.6), 100.5, LV)                  # age 1, WAIT
    assert step(s, (100.3, 100.4, 100.05, 100.3), 100.6, LV) \
        == ["RETEST BULL"]                           # age 2
    assert step(s, _bar(100.5), 100.3, LV) == []     # age 3: not yet
    assert step(s, _bar(100.6), 100.5, LV) == ["HELD BULL"]   # age 4


def test_losing_the_level_rearms_the_other_way():
    # Pine semantics, pinned: a close back THROUGH the level from
    # RETEST BULL crosses it, and the arming block runs first — the
    # machine re-arms as a fresh BROKE DOWN (which can then earn a
    # RETEST BEAR), it does not just die. No signal fires on the bar.
    s = new_state()
    step(s, _bar(100.5), 99.5, LV)
    step(s, _bar(100.6), 100.5, LV)
    step(s, (100.3, 100.4, 100.05, 100.3), 100.6, LV)
    fresh = step(s, _bar(99.7), 100.3, LV)
    assert fresh == [] and s["st"] == "BROKE DOWN" and s["age"] == 0


def test_short_mirror_fires_retest_bear():
    s = new_state()
    step(s, _bar(94.6), 95.4, LV)                    # BROKE DOWN
    step(s, _bar(94.5), 94.6, LV)                    # WAIT
    fresh = step(s, (94.8, 94.95, 94.7, 94.8), 94.5, LV)
    assert fresh == ["RETEST BEAR"]


def test_far_level_is_outside_the_search_band():
    s = new_state()
    # Price 2% below PDH: nothing within the 1% band, nothing arms.
    step(s, _bar(98.0), 97.9, LV)
    assert s["st"] == "IDLE" and s["lvl"] is None


def test_timeout_abandons_the_level():
    s = new_state()
    step(s, _bar(100.5), 99.5, LV)
    for _ in range(41):
        step(s, _bar(100.9, 100.95, 100.85), 100.9, LV)   # never touches
    assert s["st"] == "IDLE" and s["lvl"] is None


def test_writes_only_its_own_table():
    from analysis import tapebot_retest_study
    src = inspect.getsource(tapebot_retest_study)
    assert "INSERT INTO tapebot_retest_events" in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
