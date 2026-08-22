"""The sector-rotation tag and study, pinned (2026-08-22).

Eric's rotation thesis rides the house measurement pattern: a
breadth-style sector RS cache, a per-episode historical study, and a
measurement-only tag on swing specs. What this file pins:

  1. The tag contract: sector, as-of date (freshness per row), relative
     numbers, rank — and staleness declared when the cache lags.
  2. Holes are holes: no sector mapping, no cache row, each with a
     reason — never a fabricated zero (the _social_block lesson).
  3. The arming pipeline cannot see it: curate_swing/swing_geometry_ok
     signatures unchanged, and neither reads sector_state — a
     tiebreaker is a gate in disguise.
  4. The study keeps its holes: LEFT JOINs so unmapped tickers and
     cache gaps become NULL rows, not dropped episodes; and it never
     writes the paper tables (research only, by signature).

Standalone per house convention:  python3 tests/test_sector_tag.py
"""
import datetime as dt
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.sector_rs import STALE_DAYS, sector_tag  # noqa: E402


def main():
    today = dt.date(2026, 8, 22)
    row = (dt.date(2026, 8, 21), 0.02345, -0.0051, 3, 11)

    # ── Full tag contract ──
    tag = sector_tag("Energy", row, today=today)
    assert tag == {"sector": "Energy", "asof": "2026-08-21",
                   "rs_1m": 0.02345, "rs_1w": -0.0051,
                   "rank_1m": 3, "of": 11}, tag
    json.dumps(tag)   # stored as jsonb; must serialize

    # ── Staleness declared, never hidden ──
    old = (today - dt.timedelta(days=STALE_DAYS + 1), 0.01, 0.0, 5, 11)
    stale = sector_tag("Energy", old, today=today)
    assert stale.get("stale") is True
    fresh_edge = (today - dt.timedelta(days=STALE_DAYS), 0.01, 0.0, 5, 11)
    assert "stale" not in sector_tag("Energy", fresh_edge, today=today)

    # ── Holes are holes, with reasons ──
    assert sector_tag(None, row) == {"sector": None,
                                     "reason": "no_sector_mapping"}
    assert sector_tag("Energy", None) == {"sector": "Energy",
                                          "reason": "rs_unavailable"}

    # ── Arming is blind to the tag ──
    from analysis.paper_trader import curate_swing, swing_geometry_ok
    assert list(inspect.signature(curate_swing).parameters) == ["rows", "cap"]
    assert list(inspect.signature(swing_geometry_ok).parameters) == [
        "pattern", "trigger", "target", "invalid"]
    assert "sector" not in inspect.getsource(curate_swing)
    assert "sector" not in inspect.getsource(swing_geometry_ok)

    # ── The study keeps its holes and touches no paper table ──
    from analysis import sector_study
    src = inspect.getsource(sector_study)
    assert "LEFT JOIN" in src, "holes must be kept, not dropped"
    for forbidden in ("INSERT INTO paper", "UPDATE paper"):
        assert forbidden not in src, forbidden
    # One RS definition for study and tag alike (the find_defense rule).
    assert "from analysis.sector_rs import" in src

    print("ok — tag contract stable, holes carry reasons, arming is "
          "blind, study is research-only")


if __name__ == "__main__":
    main()
