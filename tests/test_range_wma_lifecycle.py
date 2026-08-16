"""Range-break and 200-week-touch lifecycles — pinned.

Born 2026-08-16, the full-catalog audit after bull_flag ("All patterns
not just bull flags" — Eric). The board census showed range_breakout,
range_breakdown, and wma_touch had NEVER shown a retest on any
timeframe. Two distinct strains of the same disease:

- _det_range_break could only say 'breakout' (price still beyond the
  edge) or 'forming' (price inside the box), so the throwback — the
  entry the desk buys — rendered as forming and the retest state was
  unreachable by construction.
- _det_wma_touch HAD a retest branch, but its 40-week qualifier walk
  started at the newest completed week, so the touch week itself
  (closing at/below the line) zeroed the run: the event erased its own
  detection. The study's event is a 40-week-QUALIFIED touch — the
  qualification precedes the touch, so up to 3 trailing touch weeks now
  get grace (each above the -3% failure line, else the verdict is in).

Standalone per house convention:  python3 tests/test_range_wma_lifecycle.py
"""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.pattern_scan import _ctx, _det_range_break, _det_wma_touch  # noqa: E402


def _bars(closes, spread=0.2):
    return [{"date": f"d{i:03d}", "close": c, "high": c + spread,
             "low": c - spread, "volume": 1_000_000}
            for i, c in enumerate(closes)]


def _box(n=130):
    """A 20-24 box: 12-bar oscillation, touches both edges in both halves."""
    return [22.0 + 2.0 * math.sin(2 * math.pi * i / 12.0) for i in range(n)]


def main():
    # ── Range break ──────────────────────────────────────────────────────
    # 1) Mid-range is nothing (unchanged).
    closes = _box()
    closes[-1] = 22.0
    assert _det_range_break(_ctx(_bars(closes), "daily")) is None

    # 2) A fresh close through the top edge is a breakout (unchanged).
    det = _det_range_break(_ctx(_bars(_box() + [24.9, 25.1]), "daily"))
    assert det and det["pattern"] == "range_breakout", det
    assert det["status"] == "breakout", det

    # 3) The throwback: broke out, now back inside the box — RETEST, the
    # state that was unreachable before (it rendered as 'forming').
    det = _det_range_break(_ctx(_bars(_box() + [24.9, 25.1, 23.9, 23.6]), "daily"))
    assert det and det["pattern"] == "range_breakout", det
    assert det["status"] == "retest", det
    assert abs(det["trigger_price"] - 24.0) < 0.5, det   # the edge survives

    # 4) The bearish mirror: breakdown then reclaim attempt — retest on
    # range_breakdown (warnings side; shorts stay retired).
    det = _det_range_break(_ctx(_bars(_box() + [19.2, 19.0, 20.3]), "daily"))
    assert det and det["pattern"] == "range_breakdown", det
    assert det["status"] == "retest", det

    # ── 200-week touch ───────────────────────────────────────────────────
    def wma_closes():
        return [50.0 * (1.0045 ** i) for i in range(259)]

    base = wma_closes()
    t = sum(base[-200:]) / 200.0            # ~the trigger the detector sees

    # 5) Qualified uptrend, line far below: not listable (unchanged).
    assert _det_wma_touch(_ctx(_bars(base + [base[-1]], 1.0), "weekly")) is None

    # 6) The touch week itself: last completed week closes just BELOW the
    # line (above the -3% failure). Pre-fix the qualifier walk zeroed on
    # this exact week and the event erased its own detection.
    closes = wma_closes()
    closes[-1] = t * 0.985                   # completed touch week
    det = _det_wma_touch(_ctx(_bars(closes + [t * 0.985], 1.0), "weekly"))
    assert det and det["status"] == "retest", det
    assert det["points"]["up_weeks"] >= 40, det

    # 7) Closed through the failure line: dead, grace does not resurrect it.
    closes = wma_closes()
    closes[-1] = t * 0.95
    assert _det_wma_touch(_ctx(_bars(closes + [t * 0.95], 1.0), "weekly")) is None

    # 8) Grace excuses only the TRAILING touch cluster: a below-line week
    # deeper in the run (with above-line weeks after it) still breaks the
    # 40-week qualification.
    closes = wma_closes()
    closes[-6] = t * 0.96      # below that week's own (earlier, lower) line
    closes[-1] = t * 0.985
    assert _det_wma_touch(_ctx(_bars(closes + [t * 0.985], 1.0), "weekly")) is None

    print("ok — range edges break out AND retest both ways, the mid-box is "
          "nothing, the 200-week touch survives its own touch week, the -3% "
          "close-through stays dead, and grace never excuses a mid-run break")


if __name__ == "__main__":
    main()
