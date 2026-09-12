"""The selection cut on the two-test engine (2026-09-12), pinned:
  1. resample_n(bars, 5) is resample5 — one definition, parameterized.
  2. trend_at reads the last COMPLETED block before the trigger minute
     and returns None inside the gate's warmup (unknown is never 0).
  3. sector_state is the sector study's cells; ETFs are a hole.
  4. Imports the trend gate from tapeentry_study; writes only
     twotest_select.
"""
import datetime as dt
import inspect
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import twotest_select as ts  # noqa: E402
from analysis.tapeentry_study import resample5  # noqa: E402

ET = ZoneInfo("America/New_York")


def _day(d, base, n=390):
    t0 = dt.datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    out = []
    px = base
    for i in range(n):
        o = px
        px = px + 0.01 * ((i % 7) - 3)
        out.append((t0 + dt.timedelta(minutes=i), o, max(o, px) + 0.02, min(o, px) - 0.02, px))
    return out


def test_resample_n_matches_resample5():
    bars = _day(dt.date(2026, 9, 10), 100.0) + _day(dt.date(2026, 9, 11), 101.0)
    a, la = ts.resample_n(bars, 5)
    b, lb = resample5(bars)
    assert a == b and la == lb
    h, lh = ts.resample_n(bars, 60)
    assert len(h) == 14 and lh[0] == 59 and h[0][0] == bars[59][0]       # 7 hourly blocks/day, last 1m at 10:29


def test_trend_at_completed_block_and_warmup():
    bars = []
    for k, day in enumerate((8, 9, 10, 11)):                   # four sessions = 28 hourly blocks
        bars += _day(dt.date(2026, 9, day), 100.0 + k)
    frames = ts.frame_trends(bars)
    tr, last, n = frames[60]
    assert n == 28
    # a trigger at 1m index 30 (10:00) sits inside hourly block 0 → no completed block → None
    assert ts.trend_at(frames[60], 30) is None
    # a trigger at 1m index 59 (10:29): block 0 completes at index 59, so it is NOT yet completed (last[0] < 59 is False)
    assert ts.trend_at(frames[60], 59) is None
    # after 21 completed hourly blocks the gate is readable: 1m index of block 21's end + 1
    i = last[21] + 1
    assert ts.trend_at(frames[60], i) in (-1, 0, 1)
    assert ts.trend_at(frames[1], 25) in (-1, 0, 1) and ts.trend_at(frames[1], 10) is None


def test_sector_state_cells():
    assert ts.sector_state(1, 0.5) == ("inflow", True)
    assert ts.sector_state(5, -0.2) == ("neutral", False)
    assert ts.sector_state(10, 0.1) == ("outflow", True)
    assert ts.sector_state(None, 0.1) == (None, None)
    assert {"SPY", "QQQ", "IWM"} == set(ts.ETFS)


def test_one_definition_and_writes_own_table():
    src = inspect.getsource(ts)
    assert "from analysis.tapeentry_study import" in src and "def trend_series(" not in src
    assert "INSERT INTO twotest_select" in src
    for forbidden in ("INSERT INTO twotest_events", "INSERT INTO paper_", "UPDATE ", "DELETE FROM"):
        assert forbidden not in src, forbidden


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
