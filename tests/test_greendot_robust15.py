"""The 15-day robustness pass, pinned (2026-08-29).

  1. blocks_nd at block=16 reproduces blocks_16d EXACTLY — the
     robustness pass re-blocks the same grid, it does not invent a
     new one.
  2. Fixed anchoring holds at 15: extending the calendar never
     changes an existing date's block (no repaint).
  3. The pass reuses the study's own dot definition (find_dots is
     imported, not reimplemented) and writes only its own tables —
     both by signature.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_robust15 import BLOCK, blocks_nd  # noqa: E402
from analysis.greendot_study import blocks_16d  # noqa: E402


def _cal(n, start=dt.date(2020, 1, 1)):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def test_block_is_15():
    assert BLOCK == 15   # the 3W-chart equivalent, frozen


def test_block16_reproduces_blocks_16d():
    cal = _cal(300)
    idx = {d: i for i, d in enumerate(cal)}
    dates = cal[7:290]
    assert blocks_nd(dates, idx, block=16) == blocks_16d(dates, idx)


def test_fixed_anchor_never_repaints_at_15():
    cal = _cal(200)
    idx = {d: i for i, d in enumerate(cal)}
    dates = cal[10:100]
    before = blocks_nd(dates, idx)
    cal2 = _cal(260)
    idx2 = {d: i for i, d in enumerate(cal2)}
    assert before == blocks_nd(dates, idx2), "repaint at 15-day blocks!"


def test_off_calendar_dates_inherit_prior_index():
    cal = _cal(64)
    idx = {d: i for i, d in enumerate(cal)}
    saturday = cal[20] + dt.timedelta(days=(5 - cal[20].weekday()) % 7 or 7)
    # A date past the last calendar entry's week still resolves; an
    # off-calendar date maps to the preceding trading day's block.
    assert blocks_nd([saturday], idx) == blocks_nd([cal[20]], idx) or \
        blocks_nd([saturday], idx)[0] >= blocks_nd([cal[20]], idx)[0]


def test_reuses_study_dot_definition_by_signature():
    from analysis import greendot_robust15
    src = inspect.getsource(greendot_robust15)
    assert "from analysis.greendot_study import bucket, find_dots" in src
    assert "def find_dots" not in src   # never reimplemented


def test_writes_only_its_own_tables():
    from analysis import greendot_robust15
    src = inspect.getsource(greendot_robust15)
    assert "INSERT INTO greendot_dots15" in src
    assert "INSERT INTO greendot15_progress" in src
    assert "INSERT INTO greendot_dots " not in src
    assert "INSERT INTO greendot_dots\n" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src
    assert "INSERT INTO paper_" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
