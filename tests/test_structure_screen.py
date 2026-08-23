"""The structure screen's lifecycle classifier, pinned (2026-08-23).

  1. Break -> retest: a CLOSE through a major shelf then a pullback
     into the band with no close back through = 'retest' (the entry
     state the desk trades).
  2. The wick rule holds both ways: a wick back through the broken
     level is NOT a failure; only a close is. And a wick through the
     level never counts as the break.
  3. A close back through after the break = 'failed' — recorded, never
     a candidate.
  4. Bearish mirror classifies symmetrically (and surfaces as
     warning-only downstream — shorts are retired).
  5. No lookahead by construction: the runner computes shelves from
     bars BEFORE the action window (pinned by source), so a level can
     never be justified by the move that broke it.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.structure_screen import RETEST_BAND, classify  # noqa: E402

D = dt.date


def _b(day, h, low, c):
    return {"date": D(2026, 8, day), "high": h, "low": low, "close": c}


LEVEL = 100.0


def test_break_then_retest():
    action = [_b(10, 99.5, 98.0, 99.0),          # inside
              _b(11, 101.2, 99.2, 101.0),        # CLOSE through -> breakout
              _b(12, 102.4, 101.6, 102.0),       # holds ABOVE the 1.5% band
              _b(13, 101.6, 100.4, 101.1)]       # low 100.4 <= 101.5 band
    v = classify(LEVEL, "resistance", action)
    assert v["state"] == "retest"
    assert v["break_date"] == D(2026, 8, 11)
    assert v["retest_date"] == D(2026, 8, 13)
    assert 100.4 <= LEVEL * (1 + RETEST_BAND)


def test_wick_never_breaks_and_wick_back_never_fails():
    # A wick above the level without a close through is NOT a break.
    v = classify(LEVEL, "resistance",
                 [_b(10, 101.5, 99.0, 99.8), _b(11, 101.9, 99.5, 99.9)])
    assert v is None
    # After a real break, a deep wick below the level is NOT a failure.
    action = [_b(10, 101.2, 99.2, 101.0),        # break
              _b(11, 101.5, 98.9, 100.6)]        # wick to 98.9, close above
    v = classify(LEVEL, "resistance", action)
    assert v["state"] == "retest"                # touched the band, held


def test_close_back_through_fails():
    action = [_b(10, 101.2, 99.2, 101.0),
              _b(11, 101.0, 99.0, 99.4)]         # CLOSE back below
    assert classify(LEVEL, "resistance", action)["state"] == "failed"


def test_bearish_mirror():
    action = [_b(10, 101.0, 99.6, 100.2),        # inside (above support)
              _b(11, 100.3, 98.5, 98.9),         # CLOSE below -> breakdown
              _b(12, 99.6, 98.2, 98.8)]          # high back into band
    v = classify(LEVEL, "support", action)
    assert v["state"] == "retest"
    assert v["break_date"] == D(2026, 8, 11)


def test_never_broken_is_none_and_no_lookahead_pinned():
    assert classify(LEVEL, "resistance",
                    [_b(10, 99.0, 97.0, 98.0)]) is None
    from analysis import structure_screen
    src = inspect.getsource(structure_screen.run_structure_screen)
    assert "shelf_bars = bars[:-BREAK_WINDOW]" in src, \
        "shelves must be computed from bars BEFORE the action window"
    # Screen only, by signature: no writes to the paper tables.
    full = inspect.getsource(structure_screen)
    for forbidden in ("INSERT INTO paper", "UPDATE paper"):
        assert forbidden not in full, forbidden


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
