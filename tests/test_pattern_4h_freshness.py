"""4h scan freshness gate: stale bars must be skipped, never detected on.

Caught live 2026-07-30: Polygon's degraded per-ticker feed served BKNG bars
ending ~Jul 24, and the scanner wrote 4h rows with last_close 177.26 on a day
BKNG traded 201.30 (MSFT/ONDS/AMZN same). A failed lookup is not data.
Standalone: python3 tests/test_pattern_4h_freshness.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.pattern_scan import _bars_fresh


def _bar(date):
    return {"date": date, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}


def test_stale_bars_rejected():
    bars = [_bar("2026-07-23"), _bar("2026-07-24")]
    assert not _bars_fresh(bars, "2026-07-29"), "bars ending 7/24 vs session 7/29 must be stale"
    print("  ok: bars ending 5 sessions back are rejected")


def test_current_bars_accepted():
    bars = [_bar("2026-07-28"), _bar("2026-07-29")]
    assert _bars_fresh(bars, "2026-07-29")
    bars.append(_bar("2026-07-30"))          # intraday scan: newest bar is today
    assert _bars_fresh(bars, "2026-07-29")
    print("  ok: bars at or past the latest session are accepted")


def test_edge_cases():
    assert not _bars_fresh([], "2026-07-29"), "no bars is never fresh"
    assert _bars_fresh([_bar("2026-07-01")], None), \
        "no session yardstick -> treat as fresh, don't blank the timeframe"
    print("  ok: empty bars rejected; missing yardstick tolerated")


if __name__ == "__main__":
    test_stale_bars_rejected()
    test_current_bars_accepted()
    test_edge_cases()
    print("test_pattern_4h_freshness: all passed")
