"""A multi-touch shelf must come out of the levels engine as one level.

Motivated 2026-08-08: Eric hand-drew a supply shelf on IREN (~41.5-42.7,
four separate rejections) that no card surfaced, because the timeline flow
only read pattern rows (trigger/target/invalidation) and never called the
levels engine. The engine itself finds shelves fine — this pins that the
clustering keeps doing so, using a synthetic series with a known answer.

Standalone per house convention:  python3 tests/test_levels_shelf.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.levels import levels_from_points, _pivots  # noqa: E402


def _bar(h, l, c):
    return {"high": h, "low": l, "close": c}


def synthetic_daily():
    """60 bars: rallies stall at ~42 three times (42.2 / 42.6 / 42.0),
    dips hold ~36 twice, close ends mid-range at 39."""
    bars = []
    path = (
        [(38 + i * 0.6, 37 + i * 0.6, 37.5 + i * 0.6) for i in range(7)]   # up to ~42
        + [(42.2, 41.0, 41.2)]                                              # touch 1
        + [(41.0 - i * 0.8, 40.0 - i * 0.8, 40.4 - i * 0.8) for i in range(6)]  # down to ~36
        + [(36.6, 35.9, 36.3)]                                              # low touch 1
        + [(37 + i * 0.9, 36 + i * 0.9, 36.5 + i * 0.9) for i in range(6)]  # up again
        + [(42.6, 41.2, 41.5)]                                              # touch 2
        + [(41.2 - i * 0.85, 40.2 - i * 0.85, 40.6 - i * 0.85) for i in range(6)]
        + [(36.4, 35.8, 36.1)]                                              # low touch 2
        + [(37 + i * 0.85, 36 + i * 0.85, 36.4 + i * 0.85) for i in range(6)]
        + [(42.0, 40.8, 41.0)]                                              # touch 3
        + [(40.5, 39.2, 39.5), (40.0, 38.8, 39.0), (39.6, 38.5, 39.0)]
    )
    for h, l, c in path:
        bars.append(_bar(round(h, 2), round(l, 2), round(c, 2)))
    return bars


def main():
    bars = synthetic_daily()
    points = _pivots(bars, "1D", 3, 3)
    assert points, "no pivots found on a series with obvious swings"

    res = levels_from_points(points, bars, current_price=39.0)
    assert "error" not in res, res
    resistance = res.get("resistance", [])
    assert resistance, "no resistance levels found above price"

    # The three stalls (42.2 / 42.6 / 42.0) must merge into ONE shelf near 42
    # with >= 3 touches — not three separate single-touch lines.
    shelf = [lv for lv in resistance if 41.0 <= lv["price"] <= 43.5
             and lv["touches"] >= 3]
    assert shelf, f"expected a >=3-touch shelf near 42, got {resistance}"
    lv = shelf[0]
    assert lv["stars"] >= 2, f"3-touch recent shelf should rate >=2 stars: {lv}"

    # And the twice-held ~36 zone must appear as support.
    support = res.get("support", [])
    dz = [s for s in support if 35.0 <= s["price"] <= 37.2 and s["touches"] >= 2]
    assert dz, f"expected a >=2-touch demand zone near 36, got {support}"

    print(f"ok — shelf {lv['price']} x{lv['touches']} ({lv['stars']}★), "
          f"demand {dz[0]['price']} x{dz[0]['touches']}")


if __name__ == "__main__":
    main()
