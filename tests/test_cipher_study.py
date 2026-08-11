"""The cipher-at-episodes study's no-lookahead seam, pinned.

The study computes indicators ONCE over a ticker's full history and then
evaluates signals at historical bars by slicing. That is only honest if
every indicator is strictly backward-looking — a single accidental
centered window or full-series normalization would leak the future into
every graded episode, and the study would quietly grade an oracle. This
test proves the equivalence the study depends on: for a deterministic
5-year series, the cipher state extracted at bar i from the full-series
computation EQUALS the state computed from scratch on bars [0..i] only.

Also pins the row contract the INSERT depends on (keys and types), and
the weekly-date locator's tolerance rules.

Standalone per house convention:  python3 tests/test_cipher_study.py
"""
import datetime as dt
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from analysis.cipher_episode_study import state_at, _frame, _locate  # noqa: E402
from analysis.oscillator import compute_oscillator  # noqa: E402


def _series(n=1300, seed=11):
    rng = random.Random(seed)
    rows, d, px = [], dt.date(2020, 1, 6), 50.0
    i = 0
    while len(rows) < n:
        if d.weekday() < 5:
            px = max(1.0, px * (1 + math.sin(i / 90) * 0.004
                                + rng.uniform(-0.02, 0.02)))
            o = px * (1 + rng.uniform(-0.008, 0.008))
            h = max(o, px) * (1 + rng.uniform(0, 0.012))
            lo = min(o, px) * (1 - rng.uniform(0, 0.012))
            v = float(rng.choice([200000, 800000, 3000000]))
            rows.append((d, round(o, 4), round(h, 4), round(lo, 4),
                         round(px, 4), v))
            i += 1
        d += dt.timedelta(days=1)
    return rows


def main():
    df = _frame(_series())
    full = compute_oscillator(df)

    for i in (90, 400, 777, 1100, len(df) - 1):
        sliced_state = state_at(full, i)
        fresh = compute_oscillator(df.iloc[:i + 1])
        fresh_state = state_at(fresh, i)
        assert sliced_state == fresh_state, (
            f"LOOKAHEAD at bar {i}: full-series slice != from-scratch "
            f"compute\n{sliced_state}\nvs\n{fresh_state}")

    st = state_at(full, len(df) - 1)
    assert set(st) == {"confluence", "direction", "rsi", "macd_hist_pos",
                       "wt2", "mf", "mf_slope_pos", "signals"}
    assert isinstance(st["confluence"], int) and 0 <= st["confluence"] <= 100
    assert isinstance(st["signals"], dict)

    # Locator: exact date hits its bar; a date within the stamped bar's
    # week resolves backward to it; a gap wider than 6 days refuses.
    idx = full.index
    exact = _locate(idx, idx[500].date())
    assert exact == 500
    assert _locate(idx, (idx[500] + pd.Timedelta(days=2)).date()) in (500, 501, 502)
    assert _locate(idx, idx[0].date() - dt.timedelta(days=30)) == -1

    print(f"ok — no lookahead at 5 probe bars, state contract stable, "
          f"locator honest (confluence at tail: {st['confluence']})")


if __name__ == "__main__":
    main()
