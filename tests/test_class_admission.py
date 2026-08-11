"""Every allowlisted class must be ABLE to arm — assert admission, not
just detection.

The bug this pins (caught 2026-08-11, two days into the live desk, by
Eric asking "just higher lows?"): neckline patterns emit measured-move
targets, so their R:R is exactly 1.0 by construction — and the flat
1.5:1 geometry gate could never admit one. inverse_hs and double_bottom
sat on the allowlist, scanned daily, scored in the 80s, and were
silently unarmable. Every unit worked as written; the integration
property "each class can pass its own gates" was asserted nowhere, so
the book ran a two-day higher-low monoculture and the declared daily-
neckline experiment never actually started.

This test encodes each class's NATIVE geometry (what its detector
actually emits) and demands swing_geometry_ok admits it. Adding a class
whose gates can never pass now fails CI the day it's written.

Standalone per house convention:  python3 tests/test_class_admission.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import (  # noqa: E402
    NECKLINE_CLASSES, SWING_CLASSES, _qlvl, swing_class_ok, swing_geometry_ok)

# Each pattern's native target geometry as its detector emits it, expressed
# as (target-trigger)/(trigger-invalid). Verified against live scan rows
# 2026-08-11: necklines exactly 1.00; higher_low 1.86-2.02; wma_touch 3.33
# (10% target over a 3% stop); the rest comfortably >= 1.5.
NATIVE_RR = {
    "higher_low": 1.9,
    "double_bottom": 1.0,
    "inverse_hs": 1.0,
    "asc_triangle": 1.6,
    "bull_flag": 2.5,
    "ema_bounce": 5.0,
    "cup_handle": 4.0,
    "range_breakout": 2.0,
    "falling_wedge": 1.8,
    "wma_touch": 10.0 / 3.0,
}


def main():
    trigger, invalid = 100.0, 90.0
    for pattern, timeframe in SWING_CLASSES:
        assert pattern in NATIVE_RR, (
            f"{pattern}: no native geometry declared — declare it here the "
            f"day the class is added, or admission is unproven")
        target = trigger + NATIVE_RR[pattern] * (trigger - invalid)
        assert swing_class_ok(pattern, timeframe)
        assert swing_geometry_ok(pattern, trigger, target, invalid), (
            f"{pattern}/{timeframe} CANNOT ARM at its own native geometry "
            f"(R:R {NATIVE_RR[pattern]:.2f}) — an allowlisted class that "
            f"can never fire is the bug this test exists to catch")

    # The gate still gates: junk geometry is refused everywhere.
    assert not swing_geometry_ok("higher_low", 100.0, 112.0, 90.0)   # 1.2 < 1.5
    assert not swing_geometry_ok("inverse_hs", 100.0, 109.0, 90.0)   # 0.9 < 0.95
    assert not swing_geometry_ok("inverse_hs", 100.0, 120.0, 100.0)  # zero risk
    assert not swing_geometry_ok("bull_flag", 100.0, 120.0, 101.0)   # inverted risk
    # Necklines admit exactly at the measured move — the whole point.
    assert swing_geometry_ok("double_bottom", 100.0, 110.0, 90.0)
    assert swing_geometry_ok("inverse_hs", 100.0, 110.0, 90.0)
    # And non-necklines do NOT get the relaxed bar.
    assert not swing_geometry_ok("higher_low", 100.0, 110.0, 90.0)

    # Setup-name quantizer: cent-level flip drift maps to one identity
    # (766.75..767.24 -> 767.0), so the one-shot-per-level rule holds.
    assert _qlvl(766.87) == _qlvl(766.9) == _qlvl(767.02) == 767.0
    assert _qlvl(766.60) == 766.5
    assert _qlvl(775.0) == 775.0

    print(f"ok — all {len(SWING_CLASSES)} classes admit at native geometry, "
          "junk still refused, level identities stable")


if __name__ == "__main__":
    main()
