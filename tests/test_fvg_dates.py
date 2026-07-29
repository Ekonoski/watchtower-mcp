"""FVG rows must carry their formation date (house rule: stamp per row).

A drawer zone without its formed-on date sends the reader hunting the whole
chart for the candles — caught live on 2026-07-29 trying to verify a MSFT 4h
zone against TrendSpider with no way to know which candles built it.
Standalone: python3 tests/test_fvg_dates.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.fvg import detect_fvgs


def _bar(date, o, h, l, c, session=None):
    b = {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": 1}
    if session:
        b["session"] = session
    return b


def _base_bars():
    # 10 quiet warmup bars, then a displacement candle, then the gap-confirming
    # bar: bars[11].low (105.5) > bars[9].high (100.5) -> bullish gap.
    bars = [_bar(f"2026-07-{d:02d}", 100, 100.5, 99.8, 100.2, "09:30")
            for d in range(1, 11)]
    bars.append(_bar("2026-07-11", 100.2, 106.2, 100.1, 106.0, "13:30"))   # displacement (born)
    bars.append(_bar("2026-07-12", 106.0, 106.6, 105.5, 106.4, "09:30"))
    return bars


def test_open_gap_carries_formed_date():
    gaps = detect_fvgs(_base_bars())
    assert gaps, "expected one bullish gap"
    g = gaps[0]
    assert g["side"] == "bullish" and g["status"] == "open"
    assert g["formed"] == "2026-07-11", g
    assert g["formed_session"] == "13:30", g
    assert g["inverted_on"] is None, g
    print("  ok: open gap stamped formed 2026-07-11 13:30")


def test_inverted_gap_carries_inversion_date():
    bars = _base_bars()
    # Close back through the gap bottom (100.5) -> inversion, not fill.
    bars.append(_bar("2026-07-13", 105.0, 105.2, 99.9, 100.0, "13:30"))
    gaps = detect_fvgs(bars)
    assert gaps and gaps[0]["status"] == "inverted", gaps
    assert gaps[0]["formed"] == "2026-07-11", gaps[0]
    assert gaps[0]["inverted_on"] == "2026-07-13", gaps[0]
    print("  ok: inverted gap stamped formed 2026-07-11, inverted 2026-07-13")


if __name__ == "__main__":
    test_open_gap_carries_formed_date()
    test_inverted_gap_carries_inversion_date()
    print("test_fvg_dates: all passed")
