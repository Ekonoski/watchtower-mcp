"""The gated ladder's pure core, pinned (2026-08-29).

  1. No reclaim → tranche 1 only (the knife gets no add money).
  2. Proof precedes action: a touch BEFORE the reclaim never fills —
     only a touch strictly AFTER the first daily close above both
     EMAs does.
  3. A level touched only before the reclaim (and never again after)
     stays unfilled — the gate is not a backfill.
  4. Writes only greendot_entry — by signature.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_ladder_gate import gated_fills  # noqa: E402

LADDER = (0.0, -0.15, -0.25)


def _flat(n, v):
    return [v] * n


def test_never_reclaimed_gets_one_tranche():
    closes = [100.0] + [70.0] * 20          # deep, never above EMAs
    lows = list(closes)
    e8 = _flat(21, 90.0)
    e21 = _flat(21, 95.0)
    fills, fc = gated_fills(100.0, lows, closes, e8, e21, 0, 20, LADDER)
    assert fills == [100.0] and fc is None


def test_touch_before_reclaim_does_not_fill():
    # Day 1-3: crash to 80 (touches -15% = 85). Day 4: reclaim close
    # above both EMAs. No touch after. Tranche 2 must stay unfilled.
    closes = [100.0, 90.0, 82.0, 84.0, 96.0, 97.0, 97.0]
    lows = [100.0, 88.0, 80.0, 83.0, 95.0, 96.0, 96.0]
    e8 = _flat(7, 92.0)
    e21 = _flat(7, 94.0)
    fills, fc = gated_fills(100.0, lows, closes, e8, e21, 0, 6, LADDER)
    assert fc == 4                          # first close above both
    assert fills == [100.0]                 # touch was pre-reclaim only


def test_touch_after_reclaim_fills():
    # Reclaim at day 2, then a pullback touches -15% at day 4.
    closes = [100.0, 93.5, 96.0, 90.0, 86.0, 92.0]
    lows = [100.0, 92.0, 95.0, 88.0, 84.0, 91.0]
    e8 = _flat(6, 93.0)
    e21 = _flat(6, 94.0)
    fills, fc = gated_fills(100.0, lows, closes, e8, e21, 0, 5, LADDER)
    assert fc == 2
    assert fills == [100.0, 85.0]           # -15% fills at its limit px
    # -25% (75.0) never touched → 2 tranches deployed.


def test_writes_only_greendot_entry():
    from analysis import greendot_ladder_gate
    src = inspect.getsource(greendot_ladder_gate)
    assert "INSERT INTO greendot_entry" in src
    assert "UPDATE " not in src and "DELETE FROM" not in src
    assert "INSERT INTO paper_" not in src
    assert "INSERT INTO greendot_dots" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
