"""Geometry must survive the entry — pinned with 2026-08-07's verified tape.

Eric, 2026-08-08: same standard, no special cases — the 1.5:1 the
spec-writer demands at the trigger is re-checked at the ACTUAL fill price
on reclaim entries, because the reclaim premium reprices the trade. All
numbers below are real Friday entries verified against recorded bars
(paper_spec_bars).

Standalone per house convention:  python3 tests/test_entry_geometry.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import (  # noqa: E402
    _entry_geometry_ok, native_geometry_ratio, swing_spec_pattern,
)


def main():
    # TNDM: 2.1:1 at the 18.16 trigger, but the earnings squeeze put the
    # real reclaim entry at 19.62 — reward 2.75 vs risk 3.48 = 0.79:1.
    # The rule refuses it. (It happened to work Friday: +0.79R. One green
    # earnings pop is not the rule's defense — ~30 resolved refusals will
    # grade it, from recorded bars.)
    ok, r = _entry_geometry_ok("long", 19.62, 16.14, 22.37)
    assert not ok and round(r, 2) == 0.79, (ok, r)

    # The other Friday reclaims all survive the re-check — modest premiums
    # leave the geometry intact. EPAC: entry 37.60, 2.50:1. BKE: 1.79:1.
    ok, r = _entry_geometry_ok("long", 37.60, 35.665, 42.43)
    assert ok and round(r, 2) == 2.50, (ok, r)
    ok, r = _entry_geometry_ok("long", 45.7164, 43.16, 50.2964)
    assert ok and round(r, 2) == 1.79, (ok, r)

    # Exactly 1.5 passes — the spec-writer's own boundary, one standard.
    ok, r = _entry_geometry_ok("long", 100.0, 98.0, 103.0)
    assert ok and r == 1.5, (ok, r)

    # Short mirror: entry 49.0, stop 53.0 (risk 4), target 40.0 (reward 9).
    ok, r = _entry_geometry_ok("short", 49.0, 53.0, 40.0)
    assert ok and r == 2.25, (ok, r)
    # Short with collapsed room: entry 42.0, stop 53.0, target 40.0.
    ok, r = _entry_geometry_ok("short", 42.0, 53.0, 40.0)
    assert not ok, (ok, r)

    # ATRC, 2026-08-12: the re-check that forgot admission had gone
    # class-aware. double_bottom weekly, trigger 42.675, stop 25.36,
    # target 59.99 — native measured-move 1:1. The reclaim entry printed
    # 42.69, a 1.5-CENT premium, and the flat 1.5 demand cancelled it as
    # "1.00:1 vs 1.5" — a bar no neckline class can ever clear. Re-checked
    # at the class's own ratio, the entry survives; the pattern comes from
    # the setup name, the only place the spec records it.
    pat = swing_spec_pattern("retest_double_bottom_weekly")
    assert pat == "double_bottom", pat
    req = native_geometry_ratio(pat)
    ok, r = _entry_geometry_ok("long", 42.69, 25.36, 59.99, req)
    assert ok and round(r, 2) == 1.00, (ok, r)
    # A premium that genuinely collapses even the native 1:1 still cancels
    # — the gate is class-aware, not gone.
    ok, r = _entry_geometry_ok("long", 45.00, 25.36, 59.99, req)
    assert not ok and round(r, 2) == 0.76, (ok, r)
    # Variable-geometry classes keep the 1.5 bar through the same path.
    assert native_geometry_ratio(swing_spec_pattern(
        "retest_higher_low_daily")) == 1.5

    print("ok — TNDM's 0.79:1 refused, modest premiums pass, 1.5 boundary "
          "passes, shorts mirror, and ATRC's 1:1 measured-move reclaim "
          "survives at its class's native ratio")


if __name__ == "__main__":
    main()
