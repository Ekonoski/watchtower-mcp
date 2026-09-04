"""Per-timeframe staleness for the intraday oscillator rows (2026-09-04):
SPY's 1h series came back ending Tuesday 05:00 ET and passed the flat
4-calendar-day guard on Friday 14:06, so a three-day-old reading was
stamped as today's beside a current QQQ row. A 1h series may be at most
one weekday behind; the 4h keeps the 4-day bar. Pinned: the SPY case is
stale, Friday-bars-on-Monday and Friday-bars-on-Tuesday-after-a-holiday
are not, the sweep names its unresolved rows, and the premarket range
has an owning job.
"""
import inspect
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import oscillator as osc  # noqa: E402
from analysis import premarket_backfill as pm  # noqa: E402


def _series(ts):
    return pd.DataFrame({"close": [1.0]}, index=[pd.Timestamp(ts).tz_localize("UTC")])


def test_1h_one_weekday_grace_only():
    fri = pd.Timestamp("2026-09-04 18:06", tz="UTC")           # Friday 14:06 ET
    # the SPY case: last bar Tuesday 09:00 UTC (05:00 ET) -> Wed, Thu missing
    assert osc._is_stale(_series("2026-09-01 09:00"), "1h", fri) is True
    # Thursday's last bar on Friday: current
    assert osc._is_stale(_series("2026-09-03 23:00"), "1h", fri) is False
    # Friday's bars read on Monday morning: current
    mon = pd.Timestamp("2026-09-07 11:00", tz="UTC")
    assert osc._is_stale(_series("2026-09-04 23:00"), "1h", mon) is False
    # Friday's bars read on Tuesday after a Monday holiday: one weekday behind, current
    tue = pd.Timestamp("2026-09-08 11:00", tz="UTC")
    assert osc._is_stale(_series("2026-09-04 23:00"), "1h", tue) is False
    # Thursday's bars read the following Tuesday: stale (Fri + Mon missing)
    assert osc._is_stale(_series("2026-09-03 23:00"), "1h", tue) is True
    # 4h keeps the calendar bar: the same Tuesday bar on Friday is NOT stale for 4h
    assert osc._is_stale(_series("2026-09-01 09:00"), "4h", fri) is False
    assert osc._is_stale(_series("2026-08-30 09:00"), "4h", fri) is True
    assert osc._is_stale(_series("2026-09-01 09:00").iloc[0:0], "1h", fri) is True


def test_fetch_uses_tf_rule_and_warns():
    src = inspect.getsource(osc.fetch_intraday_fresh)
    assert "_is_stale(df, tf)" in src
    assert "log.warning" in src and "NOT re-stamped" in src
    assert "STALE_RETRY_DAYS.get(tf" in src
    sweep = inspect.getsource(osc.refresh_stale_intraday)
    assert "stale_intraday_rows(conn)" in sweep and "unresolved" in sweep
    sched = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "alerts", "scheduler.py")).read()
    assert "refresh_stale_intraday" in sched


def test_premarket_range_has_an_owner():
    src = inspect.getsource(pm.run_today)
    assert "INSERT INTO premarket_range" in src
    assert "premarket_day_" in inspect.getsource(pm._day_claim)
    assert "complete = False" in src            # a failed ticker leaves the day unclaimed
    assert "pm_bars" in src
    catch = inspect.getsource(pm.run_catchup)
    assert "max(trade_date) FROM premarket_range" in catch
    sched = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "alerts", "scheduler.py")).read()
    assert 'id="premarket_range_daily"' in sched and "run_catchup" in sched
    # the module writes its own table only
    whole = inspect.getsource(pm)
    assert "INSERT INTO paper_" not in whole and "UPDATE paper_" not in whole


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
