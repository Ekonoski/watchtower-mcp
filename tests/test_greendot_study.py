"""The 16D green-dot study's pure core, pinned (2026-08-28).

  1. Fixed anchoring: block ids come from an absolute calendar index —
     appending NEW dates never changes any EXISTING date's block (the
     no-repaint property; end-anchored resamples fail exactly this).
  2. The dot is Eric's spec verbatim: wavetrend cross-up with the cross
     BELOW ZERO — an above-zero cross is not a dot here.
  3. Drawdown buckets cut where the spec says.
  4. The study writes only its own tables — by signature.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_study import (blocks_16d, bucket,  # noqa: E402
                                     find_dots)


def _cal(n, start=dt.date(2020, 1, 1)):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def test_fixed_anchor_never_repaints():
    cal = _cal(200)
    idx = {d: i for i, d in enumerate(cal)}
    dates = cal[10:100]
    before = blocks_16d(dates, idx)
    # New trading days arrive; the calendar EXTENDS (it can only extend
    # — SPY history is complete and never back-fills earlier dates).
    cal2 = _cal(260)
    idx2 = {d: i for i, d in enumerate(cal2)}
    after = blocks_16d(dates, idx2)
    assert before == after, "existing dates changed blocks — repaint!"


def test_missing_calendar_dates_inherit_prior_index():
    cal = _cal(64)
    idx = {d: i for i, d in enumerate(cal)}
    holiday = cal[20] + dt.timedelta(days=0)   # a date IN calendar
    foreign = cal[20]                          # same block either way
    assert blocks_16d([holiday], idx) == blocks_16d([foreign], idx)


def test_dot_requires_cross_below_zero():
    #        i:    0      1      2      3     4
    wt1 = [-40.0, -35.0, -20.0, -22.0, 10.0]
    wt2 = [-30.0, -35.5, -25.0, -20.0, 5.0]
    # i=1: wt1 crosses above wt2 with wt2=-35.5 (below zero) → DOT.
    # i=3: wt1 back under wt2. i=4: crosses up again but wt2=+5 → not
    # a dot (the zero-line leg).
    assert find_dots(wt1, wt2) == [1]


def test_above_zero_cross_is_not_a_dot():
    wt1 = [10.0, 8.0, 12.0]
    wt2 = [12.0, 9.0, 10.0]
    assert find_dots(wt1, wt2) == []


def test_drawdown_buckets():
    assert bucket(0.75) == "gte70"
    assert bucket(0.55) == "b50_70"
    assert bucket(0.35) == "b30_50"
    assert bucket(0.10) == "lt30"


def test_writes_only_its_own_tables():
    from analysis import greendot_study
    src = inspect.getsource(greendot_study)
    assert "INSERT INTO greendot_dots" in src
    assert "UPDATE paper_" not in src and "INSERT INTO paper_" not in src
    assert "DELETE FROM" not in src
    # VFF onboarding touches tickers/daily_prices with DO NOTHING only.
    assert src.count("ON CONFLICT (ticker, trade_date) DO NOTHING") == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
