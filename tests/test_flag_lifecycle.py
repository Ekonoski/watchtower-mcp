"""Flag detectors have a full lifecycle — forming, breakout, retest — pinned.

Born 2026-08-16 (Eric: "There is no way across the thousands of names we
have there are no bull flags to act on"): the original _det_bull_flag
hardcoded status='forming' AND sought the pole among ALL window bars, so
the breakout bar became its own pole-high, flag_bars fell under 3, and
the detection dissolved at the exact moment it became tradable — 443
live flags in pattern_scan, not one ever at breakout or retest. A class
that can never reach the armable status fails no test that doesn't
assert its lifecycle; this one does. The bear flag mirrors it because a
warning that vanishes at its own breakdown is blind exactly when it
matters (bearish detections warn held longs; shorts stay retired).

Standalone per house convention:  python3 tests/test_flag_lifecycle.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.pattern_scan import _ctx, _det_bear_flag, _det_bull_flag  # noqa: E402


def _bars(closes):
    return [{"date": f"d{i:03d}", "close": c, "high": c + 1.0, "low": c - 1.0,
             "volume": 1_000_000} for i, c in enumerate(closes)]


def _bull_closes():
    """70 flat bars at 100, a 10-bar pole to 125, a 5-bar flag to ~119.
    Pole high 126 (close+1), run_low 99, run 27% (>= 18% daily min),
    flag_low 118, retrace 0.30 (<= 0.5). Trigger = 126."""
    base = [100.0] * 70
    pole = [102.5 + 2.5 * i for i in range(10)]          # 102.5 .. 125.0
    flag = [123.0, 121.0, 119.0, 118.5, 119.0]
    return base + pole + flag


def main():
    # 1) Pre-breakout: the flag lists as forming with the pole as trigger.
    det = _det_bull_flag(_ctx(_bars(_bull_closes()), "daily"))
    assert det and det["status"] == "forming", det
    assert det["trigger_price"] == 126.0, det
    assert det["invalid_level"] == 117.5, det            # flag_low (low = close-1)

    # 2) A close through the pole is a BREAKOUT — the state the original
    # detector could never produce (the breakout bar became its own pole
    # and the detection returned None).
    det = _det_bull_flag(_ctx(_bars(_bull_closes() + [127.5, 128.0]), "daily"))
    assert det and det["status"] == "breakout", det
    assert det["trigger_price"] == 126.0, det            # pole survives its break

    # 3) A pullback below the trigger after the break is a RETEST — the
    # entry state the swing book buys.
    det = _det_bull_flag(
        _ctx(_bars(_bull_closes() + [127.5, 128.0, 125.5, 124.8]), "daily"))
    assert det and det["status"] == "retest", det

    # 4) A wick above the pole WITHOUT a close through is still forming —
    # the wick rule governs detection states too. (High = close+1, so a
    # 125.5 close wicks 126.5 through the 126 trigger.)
    det = _det_bull_flag(_ctx(_bars(_bull_closes() + [125.5]), "daily"))
    assert det and det["status"] == "forming", det

    # 5) A spent flag (measured move reached after the break) drops — a
    # finished trade, not an entry.
    spent = _bull_closes() + [127.5, 132.0, 140.0, 146.0, 130.0]
    det = _det_bull_flag(_ctx(_bars(spent), "daily"))
    assert det is None, det

    # 6) The bear flag mirrors: pole low, downside break, breakdown status.
    closes = [100.0] * 70 + [97.5 - 2.5 * i for i in range(10)] \
        + [77.0, 79.0, 80.0, 80.5, 80.0]
    det = _det_bear_flag(_ctx(_bars(closes), "daily"))
    assert det and det["status"] == "forming" and det["direction"] == "bearish", det
    det = _det_bear_flag(_ctx(_bars(closes + [72.5, 72.0]), "daily"))
    assert det and det["status"] == "breakout", det

    print("ok — flags form, break out, and retest like every other class; "
          "a wick through the pole is not a break, a spent flag drops, and "
          "the bear flag keeps warning through its own breakdown")


if __name__ == "__main__":
    main()
