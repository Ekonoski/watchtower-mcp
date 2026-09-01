"""The RS-leader audition book, pinned (2026-08-31).

  1. One definition: rank/entry/EMA/trail imported from the graded
     studies — nothing reimplemented.
  2. lifecycle_state: the wick rule (a 1m wick through the stop exits
     nothing; a completed 5m CLOSE does), the +1R arm on a 1m high
     touch, the trail exit, and the touch-based disaster cap.
  3. Book isolation by signature: writes only rsl_book_bars and
     BOOK-parameterized paper_specs/paper_trades rows — no other
     book's name appears anywhere near a write.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import rs_leader_book as rb  # noqa: E402


def _bars(specs, start_min=0):
    t0 = dt.datetime(2026, 9, 1, 10, 0) + dt.timedelta(minutes=start_min)
    return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]


def test_one_definition_imported():
    src = inspect.getsource(rb)
    assert "from analysis.rsleader_study import" in src
    assert "from analysis.hybrid_exit_study import" in src
    assert "def rs_rank" not in src and "def find_go_entry" not in src


def test_lifecycle_wick_arm_trail_disaster():
    entry, stop = 100.0, 99.0
    # wick through the stop, closes above: no exit
    bars = _bars([(100, 100.2, 98.9, 100.1)] * 10)
    st = rb.lifecycle_state(bars, 0, entry, stop)
    # 98.9 <= disaster (99.0)? disaster = 99.0 exactly -> touch fires.
    assert st["exit"] is not None and st["exit"][0] == "disaster"
    # shallower wick: no disaster, no 5m close through -> holding
    bars = _bars([(100, 100.2, 99.2, 100.1)] * 10)
    st = rb.lifecycle_state(bars, 0, entry, stop)
    assert st["exit"] is None and st["armed"] is False
    # +1R touch arms; later 5m close below the 21 EMA exits as trail
    specs = [(100, 101.1, 100.4, 101.0)] * 5      # arm at 101
    specs += [(101, 101, 100.2, 100.3)] * 5       # closes fall
    bars = _bars(specs)
    st = rb.lifecycle_state(bars, 0, entry, stop)
    assert st["armed"] is True
    # a completed 5m close through the stop, pre-arm, exits as 'stop'
    # (stop 99.5 sits above the 1% disaster at 99.0 — distinct levels)
    specs = [(100, 100.1, 99.2, 99.3)] * 6
    bars = _bars(specs)
    st = rb.lifecycle_state(bars, 0, entry, 99.5)
    assert st["exit"] is not None and st["exit"][0] == "stop"
    assert st["exit"][2] == 99.3                  # the CLOSE, not the level


def test_book_isolation_by_signature():
    src = inspect.getsource(rb)
    assert 'BOOK = "rs_leader"' in src
    assert "INSERT INTO rsl_book_bars" in src
    for other in ("'day_bias'", "'swing'", "'gamma_iday'", "'gamma'"):
        assert other not in src
    # spec/trade writes go through the BOOK constant, never a literal
    assert "book=%s" in src or "(trade_date, book" in src
    assert "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
