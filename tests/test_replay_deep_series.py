"""The replay's path-1 resolve seam, pinned with a 20-year series.

The bug this pins (shipped in v4, caught 2026-08-09 when the deep-window
replay finished a "20-year" run in 16 minutes): the fresh-breakout path
unpacked SIX values from a SEVEN-value _resolve, so the first such
episode on any ticker raised ValueError — and the per-ticker guard
discarded the whole ticker at DEBUG level. Deep histories hit that path
almost surely, so v5's "complete" run silently censored nearly every
20-year name (AOS, APD, APA: 5,400 bars each, zero episodes), and v4's
priors were graded on the survivors.

This test replays a deterministic 5,400-bar series and demands:
  1. no exception (the crash regression);
  2. at least one episode (a 20-year trending series with bases and
     breakouts that yields nothing means a path died silently);
  3. every emitted row has exactly 16 fields (the INSERT arity — the
     drift between _record's rows and path-1's rows is how the missing
     retest field hid inside the crash).

Standalone per house convention:  python3 tests/test_replay_deep_series.py
"""
import datetime as dt
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.pattern_backtest import _replay_ticker  # noqa: E402


def _series(n=5400, seed=7):
    rng = random.Random(seed)
    bars, d, px, i = [], dt.date(2005, 1, 3), 5.0, 0
    while len(bars) < n:
        if d.weekday() < 5:
            drift = math.sin(i / 200) * 0.01 + 0.0002
            px = max(0.5, px * (1 + drift + rng.uniform(-0.02, 0.02)))
            o = px * (1 + rng.uniform(-0.01, 0.01))
            c = px
            h = max(o, c) * (1 + rng.uniform(0, 0.015))
            lo = min(o, c) * (1 - rng.uniform(0, 0.015))
            v = float(rng.choice([0, 100, 50000, 800000]))
            if rng.random() < 0.02:            # flat illiquid bar, old-data style
                o = h = lo = c = round(px, 4)
                v = 0.0
            bars.append({"date": d, "open": round(o, 4), "high": round(h, 4),
                         "low": round(lo, 4), "close": round(c, 4), "volume": v})
            i += 1
        d += dt.timedelta(days=1)
    return bars


def main():
    bars = _series()
    evs = _replay_ticker(bars, {}, "daily")          # crash regression: must not raise
    assert evs, ("20-year trending series produced ZERO episodes — a replay "
                 "path is dying silently again")
    bad = [e for e in evs if len(e) != 16]
    assert not bad, f"row arity drift: {len(bad)} rows != 16 fields (INSERT breaks)"

    # Determinism: the same seed replays to the same episodes.
    evs2 = _replay_ticker(_series(), {}, "daily")
    assert len(evs2) == len(evs)

    dates = sorted(e[3] for e in evs)
    print(f"ok — {len(evs)} episodes from {dates[0]} to {dates[-1]}, "
          "all 16-field, no silent deaths")


if __name__ == "__main__":
    main()
