"""The walking-target shadow, pinned (2026-08-28).

  1. walk_both follows the wall in either direction; walk_toward only
     adopts levels NEARER the entry — a target may shrink its ambition,
     never extend it.
  2. Stops never walk (there is no stop parameter to walk — by design).
  3. Board moves are counted; no boards = target never moves (data).
  4. Exit semantics match live: target on touch, stop on close, eod on
     the last bar.
  5. The module writes only gamma_target_shadow — by signature.
"""
import datetime as dt
import inspect
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.target_shadow import walk_exit, _wall_field  # noqa: E402

ET = ZoneInfo("America/New_York")


def _b(h, m, o, c, hi, lo):
    return (dt.datetime(2026, 8, 28, h, m, tzinfo=ET), o, c, hi, lo)


def _ts(h, m):
    return dt.datetime(2026, 8, 28, h, m, tzinfo=ET)


def test_walk_toward_adopts_only_nearer_levels():
    # Long from 716, original target 730; wall walks down to 722 then up
    # to 735. walk_toward takes 722, refuses 735.
    bars = [_b(13, 0, 716, 718, 719, 715.8),
            _b(13, 15, 718, 721, 722.5, 717.9)]
    boards = [(_ts(12, 55), 722.0), (_ts(13, 5), 735.0)]
    px, reason, moves = walk_exit("long", 716.0, 715.0, 730.0, bars,
                                  boards, "walk_toward")
    assert (px, reason, moves) == (722.0, "target", 1)


def test_walk_both_follows_the_wall_up_and_misses():
    bars = [_b(13, 0, 716, 718, 719, 715.8),
            _b(13, 15, 718, 721, 722.5, 717.9)]
    boards = [(_ts(12, 55), 722.0), (_ts(13, 5), 735.0)]
    px, reason, moves = walk_exit("long", 716.0, 715.0, 730.0, bars,
                                  boards, "walk_both")
    # Adopted 722 before bar1, then 735 before bar2 — 722.5 high misses.
    assert reason == "eod_flat" and moves == 2 and px == 721


def test_no_boards_means_frozen_target():
    bars = [_b(13, 0, 716, 718, 730.2, 715.8)]
    px, reason, moves = walk_exit("long", 716.0, 715.0, 730.0, bars, [],
                                  "walk_both")
    assert (px, reason, moves) == (730.0, "target", 0)


def test_stop_still_closes_out_and_shorts_mirror():
    bars = [_b(13, 0, 774, 776.2, 776.5, 773.9)]
    px, reason, _ = walk_exit("short", 774.2, 776.16, 772.5, bars, [],
                              "walk_toward")
    assert reason == "stop" and px == 776.2
    # Short target walk_toward: only HIGHER levels are nearer.
    bars2 = [_b(13, 0, 774, 773.5, 774.5, 773.2)]
    boards2 = [(_ts(12, 55), 773.4)]
    px2, reason2, moves2 = walk_exit("short", 774.2, 776.16, 772.5,
                                     bars2, boards2, "walk_toward")
    assert (px2, reason2, moves2) == (773.4, "target", 1)


def test_wall_field_maps_setups():
    assert _wall_field("flip_hold_716.5") == "call_wall"
    assert _wall_field("wall_fade_775") == "gamma_flip"
    assert _wall_field("late_retest_pdh") is None


def test_writes_only_its_own_table():
    from analysis import target_shadow
    src = inspect.getsource(target_shadow)
    assert "INSERT INTO gamma_target_shadow" in src
    assert "UPDATE paper_" not in src and "INSERT INTO paper_" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
