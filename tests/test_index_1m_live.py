"""The live index 1m tape (2026-09-04), pinned: only completed RTH bars
land (the forming minute never does), the four names are the liquid
set, the insert is first-seen-wins, and no book table is touched.
"""
import datetime as dt
import inspect
import os
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import index_1m_live as il  # noqa: E402


def test_completed_rth_only():
    et = ZoneInfo("America/New_York")
    ms = lambda h, m: int(dt.datetime(2026, 9, 4, h, m, tzinfo=et).timestamp() * 1000)
    aggs = [SimpleNamespace(timestamp=ms(9, 29), open=1, high=1, low=1, close=1, volume=1),
            SimpleNamespace(timestamp=ms(9, 30), open=1, high=1, low=1, close=1, volume=1),
            SimpleNamespace(timestamp=ms(9, 31), open=1, high=1, low=1, close=1, volume=None)]
    cutoff = dt.datetime(2026, 9, 4, 9, 31, tzinfo=et)      # it is 9:31:20
    rows = il.completed_rth_rows(aggs, "SPY", et, cutoff)
    assert [r[1].time() for r in rows] == [dt.time(9, 30)]  # 9:29 premarket, 9:31 forming
    assert rows[0][2] == dt.date(2026, 9, 4)


def test_scope():
    assert set(il.LIVE_NAMES) == {"SPY", "QQQ", "IWM", "AMD"} and il.TABLE == "liquid_1m_bars"
    src = inspect.getsource(il)
    assert "ON CONFLICT (ticker, ts) DO NOTHING" in src
    # the docstring may NAME the book tables (to say the rule they set);
    # the code may never read or write them
    for forbidden in ("INTO paper_", "FROM paper_", "INTO rsl_book_bars", "FROM rsl_book_bars"):
        assert forbidden not in src
    sched = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "alerts", "scheduler.py")).read()
    assert 'id="index_1m_live"' in sched


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
