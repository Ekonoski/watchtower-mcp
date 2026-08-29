"""The EMA-reclaim entry variants, pinned (2026-08-29).

  1. The EMA is the standard recursion (alpha 2/(n+1), first-value
     seed).
  2. The trigger is Eric's rule VERBATIM: price above BOTH EMAs — no
     cross requirement. A tape where the 8 never crosses the 21 still
     fires the moment price clears both; price above only one never
     fires.
  3. The search starts after the dot, never on it.
  4. Writes only greendot_entry — by signature.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_ema_entry import ema, find_above_both  # noqa: E402


def test_ema_recursion():
    e = ema([10.0, 10.0, 10.0], 8)
    assert e == [10.0, 10.0, 10.0]
    e2 = ema([10.0, 19.0], 8)          # k = 2/9
    assert abs(e2[1] - (19 * 2 / 9 + 10 * 7 / 9)) < 1e-9
    assert ema([], 8) == []


def test_price_above_both_no_cross_needed():
    # A falling tape where ema8 stays BELOW ema21 the whole way (no
    # golden cross ever) — price popping above both still fires.
    closes = [100, 90, 80, 70, 60, 50, 92, 95]
    e8, e21 = ema(closes, 8), ema(closes, 21)
    i = find_above_both(closes, e8, e21, 0, len(closes) - 1)
    assert i == 6                       # 92 clears both averages
    assert e8[i] < e21[i], "fixture must have NO cross — that's the point"


def test_price_above_one_ema_is_not_enough():
    # Price recovers above the faster 8 but stays under the 21.
    closes = [100.0] * 30 + [40.0] * 7 + [60.0]
    e8, e21 = ema(closes, 8), ema(closes, 21)
    last = len(closes) - 1
    assert closes[last] > e8[last] and closes[last] < e21[last]
    assert find_above_both(closes, e8, e21, 30, last) is None


def test_search_starts_after_the_dot():
    closes = [50, 100, 100, 100]        # index 1 qualifies instantly
    e8, e21 = ema(closes, 8), ema(closes, 21)
    # Dot AT index 1: the dot bar itself must not be the entry.
    assert find_above_both(closes, e8, e21, 1, 3) == 2
    # Window end respected.
    assert find_above_both(closes, e8, e21, 3, 3) is None


def test_week_ends_are_completed_weeks_only():
    import datetime as dt
    from analysis.greendot_ema_entry import week_end_indices
    # Two full weeks (Mon-Fri) then a Monday of week 3.
    d0 = dt.date(2024, 1, 1)   # a Monday
    dates = [d0 + dt.timedelta(days=k) for k in
             (0, 1, 2, 3, 4, 7, 8, 9, 10, 11, 14)]
    ends = week_end_indices(dates)
    # Week 1 ends at index 4, week 2 at index 9; week 3 (in progress,
    # one day) is EXCLUDED — a half-week close can never trigger.
    assert ends == [4, 9]
    assert week_end_indices(dates[:5]) == []   # only one (unproven) week


def test_writes_only_greendot_entry():
    from analysis import greendot_ema_entry
    src = inspect.getsource(greendot_ema_entry)
    assert "INSERT INTO greendot_entry" in src
    assert "UPDATE " not in src and "DELETE FROM" not in src
    assert "INSERT INTO paper_" not in src
    assert "INSERT INTO greendot_dots" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
