"""
Tests for the structure-shift detectors (higher_low / lower_high), added at
ENGINE_VERSION 14.

What these pin:

- geometry: a decline → swing low → bounce → pullback holding ABOVE the
  prior low detects as higher_low with trigger = the interim swing high,
  invalid = the higher low itself, target = trigger + (trigger − low1).
- status transitions: 'forming' below the trigger, 'breakout' after a
  recent close through it.
- the double-bottom complement: twin lows (within tol) must NOT fire
  higher_low — that pair belongs to the double bottom.
- death by undercut: a late bar under the higher low kills the pattern
  (invalid IS the higher low — an undercut pattern must not render).
- the bearish mirror (lower_high), same geometry upside down.
- timeframes: the detector runs on weekly, daily, and 4h configs.

Run: python3 tests/test_pattern_structure_shift.py   (or pytest)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.pattern_scan import detect_patterns  # noqa: E402


def _bar(i, close, high=None, low=None):
    return {"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "close": close,
            "high": high if high is not None else close + 0.3,
            "low": low if low is not None else close - 0.3,
            "volume": 1_000_000}


def _series(path):
    """path: list of (n_bars, start, end) legs, linearly interpolated."""
    bars, x = [], None
    for n, a, b in path:
        for i in range(n):
            x = a + (b - a) * i / max(n - 1, 1)
            bars.append(x)
    return bars


def _higher_low_bars(last_leg_end=86.0, undercut=None):
    """~92 daily bars: flat 100 → decline to 80 (L1) → bounce to 88 (M) →
    pullback to 84 (L2, the higher low) → turn up to last_leg_end."""
    closes = _series([(42, 100, 100), (24, 100, 80.5), (1, 80, 80),
                      (7, 81.5, 88), (1, 88, 88), (7, 87, 84.4),
                      (1, 84, 84), (8, 84.8, last_leg_end)])
    bars = [_bar(i, c) for i, c in enumerate(closes)]
    if undercut is not None:
        bars.append(_bar(len(bars), undercut + 0.4, low=undercut))
    return bars


def _get(dets, name):
    return next((d for d in dets if d["pattern"] == name), None)


def test_higher_low_forming():
    dets = detect_patterns(_higher_low_bars(last_leg_end=86.0), "daily")
    hl = _get(dets, "higher_low")
    assert hl is not None, f"higher_low not detected; got {[d['pattern'] for d in dets]}"
    assert hl["direction"] == "bullish"
    assert hl["status"] == "forming", hl["status"]
    assert abs(hl["trigger_price"] - 88.3) < 0.5, hl["trigger_price"]
    assert abs(hl["invalid_level"] - 84.0) < 0.5, hl["invalid_level"]
    # target = trigger + (trigger − L1) ≈ 88.3 + 8.3
    assert abs(hl["target"] - (2 * hl["trigger_price"] - 80.0)) < 0.6, hl["target"]
    assert hl["points"]["low2"]["price"] > hl["points"]["low1"]["price"]
    print("  ok: higher_low forming — trigger/invalid/target geometry")


def test_higher_low_breakout():
    bars = _higher_low_bars(last_leg_end=86.0)
    for c in (89.0, 90.0, 90.5):
        bars.append(_bar(len(bars), c))
    hl = _get(detect_patterns(bars, "daily"), "higher_low")
    assert hl is not None, "higher_low vanished on breakout"
    assert hl["status"] == "breakout", hl["status"]
    print("  ok: higher_low breakout after close through the trigger")


def test_undercut_kills_it():
    dets = detect_patterns(_higher_low_bars(undercut=83.2), "daily")
    assert _get(dets, "higher_low") is None, \
        "higher_low survived an undercut of the higher low (invalid level)"
    print("  ok: undercut of the higher low kills the pattern")


def test_twin_lows_belong_to_double_bottom():
    # Second low within twin tolerance of the first (80.5 vs 80): the pair is
    # the double bottom's, not a higher low.
    closes = _series([(42, 100, 100), (24, 100, 80.5), (1, 80, 80),
                      (7, 81.5, 88), (1, 88, 88), (7, 87, 81),
                      (1, 80.5, 80.5), (8, 81.3, 86)])
    dets = detect_patterns([_bar(i, c) for i, c in enumerate(closes)], "daily")
    assert _get(dets, "higher_low") is None, \
        "twin lows fired higher_low — that pair belongs to double_bottom"
    print("  ok: twin lows do not fire higher_low (double-bottom complement)")


def test_lower_high_mirror():
    # Mirror: flat 60 → rally to 80 (H1) → dip to 72 (M) → bounce to 76
    # (H2, the lower high) → roll over.
    closes = _series([(42, 60, 60), (24, 60, 79.5), (1, 80, 80),
                      (7, 78.5, 72.4), (1, 72, 72), (7, 72.8, 75.6),
                      (4, 76, 76), (8, 75.2, 74.0)])
    dets = detect_patterns([_bar(i, c) for i, c in enumerate(closes)], "daily")
    lh = _get(dets, "lower_high")
    assert lh is not None, f"lower_high not detected; got {[d['pattern'] for d in dets]}"
    assert lh["direction"] == "bearish"
    assert lh["status"] == "forming", lh["status"]
    assert abs(lh["trigger_price"] - 72.0) < 0.6, lh["trigger_price"]
    assert abs(lh["invalid_level"] - 76.0) < 0.6, lh["invalid_level"]
    print("  ok: lower_high mirror — geometry and direction")


def test_runs_on_all_timeframes():
    # Same shape, stretched/compressed to satisfy each config's min_bars and
    # scaled thresholds. Weekly needs 1.7x depth; use a deeper structure.
    for tf, pre, depth_lo in (("weekly", 20, 62.0), ("daily", 30, 80.0), ("4h", 20, 80.0)):
        closes = _series([(pre, 100, 100), (24, 100, depth_lo + 0.5),
                          (1, depth_lo, depth_lo),
                          (8, depth_lo + 2, depth_lo + 12), (1, depth_lo + 12, depth_lo + 12),
                          (8, depth_lo + 11, depth_lo + 6.4),
                          (1, depth_lo + 6, depth_lo + 6),
                          (9, depth_lo + 6.8, depth_lo + 9.5)])
        dets = detect_patterns([_bar(i, c) for i, c in enumerate(closes)], tf)
        hl = _get(dets, "higher_low")
        assert hl is not None, f"higher_low missing on {tf}"
        assert hl["timeframe"] == tf
    print("  ok: higher_low detects on weekly, daily, and 4h")


if __name__ == "__main__":
    test_higher_low_forming()
    test_higher_low_breakout()
    test_undercut_kills_it()
    test_twin_lows_belong_to_double_bottom()
    test_lower_high_mirror()
    test_runs_on_all_timeframes()
    print("all structure-shift tests passed")
