"""Shape matching — the pure core, pinned.

Born 2026-08-15 from the CEG/SNAP lesson: CEG matched SNAP's weekly
numbers to the decimal at the last bar (and tracked its 12-week path),
yet the charts READ differently — the eye matches months of shape:
mound count, whether the second mound is shallower, the staircase. So
matching is done on trajectories in fixed component units, and the
mound structure must agree before a numeric twin can rank.

Standalone per house convention:  python3 tests/test_shape_match.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.shape_match import (  # noqa: E402
    path_distance, wave_trough_structure)


def main():
    t = np.linspace(0, 4 * np.pi, 40)

    # Identical paths → distance 0 on every component.
    ref = {"wt2": -30 + 20 * np.sin(t), "rsi": np.full(40, 50.0),
           "pctr": -50 + 30 * np.sin(t), "mf": -5 + 3 * np.sin(t),
           "wt1": -28 + 20 * np.sin(t),
           "macd_pct": -4 + np.sin(t), "hist_pct": 0.5 * np.sin(t)}
    d, per = path_distance(ref, {k: v.copy() for k, v in ref.items()})
    assert d == 0.0, (d, per)

    # A constant offset registers in scale units: +30 on wt2 (scale 60)
    # contributes 0.5 to that component.
    cand = {k: v.copy() for k, v in ref.items()}
    cand["wt2"] = cand["wt2"] + 30
    d, per = path_distance(ref, cand)
    assert abs(per["wt2"] - 0.5) < 1e-6, per

    # Phase-flipped waves (same numbers at SOME bar, opposite path) are
    # far — the CEG/SNAP lesson in miniature: state can match while the
    # path does not.
    flipped = {k: v.copy() for k, v in ref.items()}
    flipped["wt2"] = -30 + 20 * np.sin(t + np.pi)
    d_flip, per_flip = path_distance(ref, flipped)
    assert per_flip["wt2"] > 0.35, per_flip   # the flipped wave path is FAR
    assert d_flip > 0.04, d_flip              # and it moves the overall score

    # Mound structure: two rising troughs read as the visual higher low;
    # a single-grind path does not.
    x = np.linspace(0, np.pi, 16)[1:-1]          # open interval — no flat joints
    two_mounds = np.concatenate([
        [-5.0], -5 - 55 * np.sin(x), [-5.0],     # deep mound to ~-60
        -5 - 35 * np.sin(x), [-5.0]])            # shallower mound to ~-40
    st = wave_trough_structure(two_mounds)
    assert st["n_troughs"] == 2 and st["rising"] is True, st
    one_grind = np.concatenate([np.linspace(0, -55, 30), np.linspace(-53, -40, 10)])
    st1 = wave_trough_structure(one_grind)
    assert st1["n_troughs"] == 1 and st1["rising"] is False, st1

    # The CHWY-vs-SNAP calibration (2026-08-15): near-equal twin mounds
    # (−69 → −68.5, half a point) are NOT the rising-staircase look —
    # material lift is required, and the lift is reported for audit.
    twin_mounds = np.concatenate([
        [-5.0], -5 - 64 * np.sin(x), [-5.0],
        -5 - 63.5 * np.sin(x), [-5.0]])
    st2 = wave_trough_structure(twin_mounds)
    assert st2["n_troughs"] == 2 and st2["rising"] is False, st2
    assert abs(st2["trough_lift"] - 0.5) < 0.2, st2

    # Short overlaps still score (over the shared tail), and an empty
    # candidate is infinite distance, never a silent zero.
    short = {"wt2": ref["wt2"][-12:]}
    d, per = path_distance(ref, short)
    assert np.isfinite(d) and "wt2" in per, (d, per)
    d, per = path_distance(ref, {})
    assert not np.isfinite(d), d

    print("ok — identical paths score zero, offsets scale correctly, "
          "phase-flipped state-twins are far, mound structure separates "
          "twin-mound charts from single grinds, short overlaps score "
          "honestly and empty candidates are infinite, never zero")


if __name__ == "__main__":
    main()
