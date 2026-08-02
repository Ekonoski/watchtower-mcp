"""Backtest v4 units: weekly aggregation + native retest measurement.

Standalone: python3 tests/test_backtest_weekly_retest.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.pattern_backtest import _weekly_bars, _resolve


def _b(d, o, h, lo, c, v=1.0):
    return {"date": d, "open": o, "high": h, "low": lo, "close": c, "volume": v}


def test_weekly_bars_aggregate_iso_weeks():
    # Mon Jul 6 .. Fri Jul 10 2026 = one ISO week; Mon Jul 13 starts the next.
    daily = [
        _b(date(2026, 7, 6), 10, 12, 9, 11, 100),
        _b(date(2026, 7, 8), 11, 15, 10, 14, 100),
        _b(date(2026, 7, 10), 14, 14.5, 12, 13, 100),
        _b(date(2026, 7, 13), 13, 16, 13, 15, 100),
    ]
    w = _weekly_bars(daily)
    assert len(w) == 2, w
    wk = w[0]
    assert wk["open"] == 10 and wk["high"] == 15 and wk["low"] == 9
    assert wk["close"] == 13 and wk["volume"] == 300
    assert wk["date"] == date(2026, 7, 10), "weekly bar dated by its last session"
    print("  ok: ISO-week aggregation (OHLCV + last-session date)")


def test_resolve_records_retest_bar():
    # Breakout bar j=0 closes above trigger 100; entry next bar.
    # Bar 2 dips its low back to the trigger (the second chance), then target.
    bars = [
        _b(date(2026, 7, 6), 99, 101, 98, 101),     # j: breakout close
        _b(date(2026, 7, 7), 101, 103, 100.5, 102),  # entry bar, no touch
        _b(date(2026, 7, 8), 102, 102.5, 99.9, 101),  # retest: low <= 100
        _b(date(2026, 7, 9), 101, 110.5, 101, 110),   # target 110 hit
    ]
    outcome, bto, w1, b1, rr, retest, end = _resolve(
        bars, 0, entry=101.0, target=110.0, invalid=95.0,
        direction="bullish", trigger=100.0)
    assert outcome == "target"
    assert retest == 2, f"expected retest on bar 2, got {retest}"
    print("  ok: bullish retest recorded at the first trigger re-touch")


def test_resolve_no_retest_is_none():
    bars = [
        _b(date(2026, 7, 6), 99, 101, 98, 101),
        _b(date(2026, 7, 7), 101, 104, 100.6, 103),
        _b(date(2026, 7, 8), 103, 110.5, 102, 110),
    ]
    outcome, bto, w1, b1, rr, retest, end = _resolve(
        bars, 0, entry=101.0, target=110.0, invalid=95.0,
        direction="bullish", trigger=100.0)
    assert outcome == "target" and retest is None
    print("  ok: runaway winner records retest=None")


if __name__ == "__main__":
    test_weekly_bars_aggregate_iso_weeks()
    test_resolve_records_retest_bar()
    test_resolve_no_retest_is_none()
    print("test_backtest_weekly_retest: all passed")
