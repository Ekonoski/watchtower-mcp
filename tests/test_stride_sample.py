"""The backtest's universe sampler, pinned against silent drift.

Two codebases must sample identically: analysis/pattern_backtest.py
(this repo) draws the replay universe, and the watchtower repo's
ingestion/backfill_daily_history.py backfills deep history for the SAME
names using a byte-for-byte copy of this function. If the algorithms
drift, the deep history lands under names the replay never reads and
v5's regime coverage silently thins. These exact vectors are the
contract — change them only in both places at once.

Standalone per house convention:  python3 tests/test_stride_sample.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.pattern_backtest import _stride_sample  # noqa: E402


def main():
    u10 = [f"T{i:02d}" for i in range(10)]
    # stride 10/4 = 2.5 → indices int(0), int(2.5), int(5.0), int(7.5)
    assert _stride_sample(u10, 4) == ["T00", "T02", "T05", "T07"], \
        _stride_sample(u10, 4)

    # at or under the cap: identity, same object contents, full coverage
    assert _stride_sample(u10, 10) == u10
    assert _stride_sample(u10, 50) == u10
    assert _stride_sample([], 5) == []

    # deterministic: same input → same sample, every time
    u = [f"S{i:04d}" for i in range(8836)]   # the real universe size today
    a, b = _stride_sample(u, 2500), _stride_sample(u, 2500)
    assert a == b and len(a) == 2500
    assert a[0] == "S0000" and a[-1] == u[int(2499 * (8836 / 2500))], a[-1]

    print("ok — stride sampler deterministic and pinned; the watchtower "
          "backfill's copy must match these exact vectors")


if __name__ == "__main__":
    main()
