"""
gamma_iday book: the intraday re-armer must arm new levels as the board
moves, cancel abandoned ones, never touch open trades, and never re-arm a
level that already had its shot today. Setup names live on the half-point
grid (_qlvl, 2026-08-11): cent drift is one level, a half-point move is a
new one — asserted here from both sides.

Standalone:  python3 tests/test_paper_intraday.py    # or: pytest tests/
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.paper_trader import build_gamma_specs, diff_intraday_specs  # noqa: E402

DAY = dt.date(2026, 8, 6)
BOARD = [("SPY", 768.96, 775.0, 750.0, 765.46, 2.415, "pinning")]


def _fresh():
    specs, _ = build_gamma_specs(DAY, BOARD, "armed", book="gamma_iday")
    return specs


def test_build_gamma_specs_book_param():
    specs = _fresh()
    assert specs and all(s[1] == "gamma_iday" for s in specs)
    assert {s[4] for s in specs} == {"wall_fade_775", "flip_hold_765.5"}


def test_first_cycle_arms_everything():
    to_insert, to_cancel = diff_intraday_specs([], _fresh())
    assert len(to_insert) == 2 and to_cancel == []


def test_unchanged_board_is_a_no_op():
    existing = [(1, "SPY", "wall_fade_775", "armed"),
                (2, "SPY", "flip_hold_765.5", "armed")]
    to_insert, to_cancel = diff_intraday_specs(existing, _fresh())
    assert to_insert == [] and to_cancel == []


def test_cent_drift_is_the_same_level():
    # The #179 lesson: 765.46 -> 765.54 is ONE level wobbling (both quantize
    # to 765.5). Before name quantization this minted ~30 phantom
    # cancel/re-arm pairs in a day; now it must be a no-op.
    existing = [(1, "SPY", "wall_fade_775", "armed"),
                (2, "SPY", "flip_hold_765.5", "armed")]
    drift = [("SPY", 768.9, 775.0, 750.0, 765.54, 2.4, "pinning")]
    fresh, _ = build_gamma_specs(DAY, drift, "armed", book="gamma_iday")
    to_insert, to_cancel = diff_intraday_specs(existing, fresh)
    assert to_insert == [] and to_cancel == []


def test_board_move_cancels_armed_and_arms_new_level():
    # Flip migrated 765.46 -> 766.2 (765.5 -> 766 on the half-point grid —
    # a REAL move, not cent drift): old flip-hold cancels, new one arms,
    # the untouched wall fade stays put.
    existing = [(1, "SPY", "wall_fade_775", "armed"),
                (2, "SPY", "flip_hold_765.5", "armed")]
    moved = [("SPY", 768.5, 775.0, 750.0, 766.2, 2.1, "pinning")]
    fresh, _ = build_gamma_specs(DAY, moved, "armed", book="gamma_iday")
    to_insert, to_cancel = diff_intraday_specs(existing, fresh)
    assert [s[4] for s in to_insert] == ["flip_hold_766"]
    assert to_cancel == [2]


def test_triggered_specs_are_never_cancelled():
    # Open trade at a level the board has left: the trade manages to exit.
    existing = [(2, "SPY", "flip_hold_765.5", "triggered")]
    moved = [("SPY", 768.5, 775.0, 750.0, 766.2, 2.1, "pinning")]
    fresh, _ = build_gamma_specs(DAY, moved, "armed", book="gamma_iday")
    _, to_cancel = diff_intraday_specs(existing, fresh)
    assert to_cancel == []


def test_one_shot_per_level_per_day():
    # A cancelled level that comes back onto the board does not re-arm.
    existing = [(1, "SPY", "wall_fade_775", "armed"),
                (2, "SPY", "flip_hold_765.5", "cancelled")]
    to_insert, to_cancel = diff_intraday_specs(existing, _fresh())
    assert to_insert == [] and to_cancel == []


def test_curate_swing_dedupes_and_caps():
    from analysis.paper_trader import curate_swing
    rows = [("AAA", "daily", "higher_low", "long", 10, 15, 9, 80),
            ("AAA", "weekly", "higher_low", "long", 10, 16, 9, 72),   # weekly beats daily
            ("BBB", "daily", "double_bottom", "long", 20, 30, 18, 95),
            ("CCC", "daily", "higher_low", "long", 5, 8, 4.5, 71)]
    kept, dropped = curate_swing(rows, cap=2)
    assert [(r[0], r[1]) for r in kept] == [("BBB", "daily"), ("AAA", "weekly")]
    assert dropped == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
