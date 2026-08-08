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

from analysis.paper_trader import _entry_geometry_ok  # noqa: E402


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

    print("ok — TNDM's 0.79:1 refused, modest premiums pass, 1.5 boundary "
          "passes, shorts mirror")


if __name__ == "__main__":
    main()
