"""The 3d resample is repaint-proof — pinned.

Born 2026-08-16 with the '3d' scan timeframe (the BW-3D archetype).
The on-demand resample_days is END-anchored — fine for a one-off read,
fatal for STORED rows: every new session re-buckets the whole series,
so yesterday's completed 3d bar changes composition today and any
signal graded on it repaints. resample_sessions buckets by business-day
ordinal since a fixed epoch: bars are stable as the fetch window
slides, holidays leave shorter bars (like a holiday-shortened week),
and the in-progress bucket is dropped per the weekly partial-bar rule.

Standalone per house convention:  python3 tests/test_resample_3d.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.oscillator import resample_sessions  # noqa: E402


def _daily(dates):
    n = len(dates)
    c = np.linspace(100.0, 120.0, n)
    return pd.DataFrame({"open": c - 0.5, "high": c + 1.0, "low": c - 1.0,
                         "close": c, "volume": np.full(n, 1e6)}, index=dates)


def main():
    # A long-past window so the last bucket is unambiguously complete.
    dates = pd.bdate_range("2024-01-01", periods=63)
    df = _daily(dates)

    r_full = resample_sessions(df, 3, drop_partial=False)
    # 1) Buckets are 3 sessions on a holiday-free calendar (the epoch
    # anchor may leave a partial bucket at either EDGE of the window —
    # that's the price of stability); bar stamps are each bucket's last
    # session.
    assert all(ts in set(dates) for ts in r_full.index)
    mid_sizes = {round(v / 1e6) for v in r_full["volume"].iloc[1:-1]}
    assert mid_sizes == {3}, mid_sizes

    # 2) THE REPAINT TEST: slide the window start by 5 sessions — every
    # shared completed bar must be IDENTICAL (same stamp, same OHLC). An
    # end-anchored grouping fails this immediately.
    r_slid = resample_sessions(df.iloc[5:], 3, drop_partial=False)
    shared = r_full.index.intersection(r_slid.index)
    assert len(shared) >= 18, len(shared)
    # The slid window's LEADING bucket may be truncated by the cut (same
    # stamp, fewer sessions — an oldest-edge artifact hundreds of bars
    # from the live read); every bar after it must be IDENTICAL. An
    # end-anchored grouping fails this on every bar.
    body = shared[1:]
    pd.testing.assert_frame_equal(r_full.loc[body], r_slid.loc[body])
    assert set(r_slid.index[1:]) <= set(r_full.index)

    # 3) A holiday leaves a 2-session bar; neighboring buckets unchanged
    # (edge buckets excluded — they may be partial by the anchor).
    hol = dates.delete(30)                   # drop one session mid-window
    r_hol = resample_sessions(_daily(hol), 3, drop_partial=False)
    sizes = [round(v / 1e6) for v in r_hol["volume"].iloc[1:-1]]
    assert sizes.count(2) == 1 and sizes.count(3) == len(sizes) - 1, sizes

    # 4) drop_partial keeps a long-completed final bucket (nothing to drop).
    r_dp = resample_sessions(df, 3, drop_partial=True)
    assert r_dp.index[-1] == r_full.index[-1]

    print("ok — epoch-anchored buckets are stable under window slides "
          "(no repaint), holidays leave honest 2-session bars, and "
          "long-completed final buckets survive drop_partial")


if __name__ == "__main__":
    main()
