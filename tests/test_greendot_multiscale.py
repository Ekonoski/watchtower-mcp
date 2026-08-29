"""The multiscale dot study, pinned (2026-08-29).

  1. Weekly bars are completed ISO weeks only — the in-progress week
     is dropped (repaint guard), and each bar carries the LAST daily
     index of its week so entries land on real closes.
  2. The dot definition is the study's own (find_dots imported, never
     reimplemented) — by signature.
  3. Writes only its own tables — by signature. The 16D record
     (greendot_dots) is never touched.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_multiscale import SCALES, week_bars  # noqa: E402


def test_scales_are_daily_and_weekly():
    assert SCALES == ("daily", "weekly")   # 16D lives in greendot_dots


def test_week_bars_completed_weeks_only():
    d0 = dt.date(2024, 1, 1)   # a Monday
    days = [0, 1, 2, 3, 4, 7, 8, 9, 10, 11, 14]
    dates = [d0 + dt.timedelta(days=k) for k in days]
    closes = [float(i) for i in range(len(dates))]
    bars = week_bars(dates, closes, closes, closes, closes)
    assert len(bars) == 2                  # week 3 (one day) dropped
    assert bars[0]["di"] == 4 and bars[0]["c"] == 4.0
    assert bars[1]["di"] == 9 and bars[1]["c"] == 9.0


def test_week_bars_aggregate_ohlc():
    d0 = dt.date(2024, 1, 1)
    dates = [d0 + dt.timedelta(days=k) for k in (0, 1, 2, 3, 4, 7)]
    opens = [10.0, 11, 12, 13, 14, 99]
    highs = [15.0, 11, 20, 13, 14, 99]
    lows = [9.0, 8, 12, 13, 5, 99]
    closes = [10.5, 11, 12, 13, 13.5, 99]
    bars = week_bars(dates, closes, opens, highs, lows)
    assert len(bars) == 1
    b = bars[0]
    assert b["o"] == 10.0 and b["h"] == 20.0 and b["l"] == 5.0 \
        and b["c"] == 13.5


def test_reuses_study_dot_definition_by_signature():
    from analysis import greendot_multiscale
    src = inspect.getsource(greendot_multiscale)
    assert "from analysis.greendot_study import find_dots" in src
    assert "def find_dots" not in src


def test_writes_only_its_own_tables():
    from analysis import greendot_multiscale
    src = inspect.getsource(greendot_multiscale)
    assert "INSERT INTO greendot_dots_ms" in src
    assert "INSERT INTO greendot_ms_progress" in src
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
