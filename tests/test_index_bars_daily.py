"""The research bar tables have an owning job (2026-09-03): the 16:20
appender covers the 15m index record AND the two 1m tables that froze
at 8/31; RTH filtering keeps only 9:30..last-bar-start; the insert is
idempotent on (ticker, ts); pagination goes through list_aggs.
"""
import datetime as dt
import inspect
import os
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import index_bars_daily as ib  # noqa: E402


def test_targets_cover_every_frozen_table():
    tables = {t[0] for t in ib.TARGETS}
    assert tables == {"index_intraday_bars", "mag7_1m_bars", "liquid_1m_bars"}
    by = {t[0]: t for t in ib.TARGETS}
    assert set(by["mag7_1m_bars"][1]) == set(ib.MAG7) and by["mag7_1m_bars"][2] == 1
    assert set(by["liquid_1m_bars"][1]) == {"AMD", "IWM", "QQQ", "SPY"}
    assert by["index_intraday_bars"][2] == 15 and by["index_intraday_bars"][3] == dt.time(15, 45)
    assert by["mag7_1m_bars"][3] == dt.time(15, 59)


def test_rth_rows_filters_the_session():
    et = ZoneInfo("America/New_York")
    ms = lambda h, m: int(dt.datetime(2026, 9, 2, h, m, tzinfo=et).timestamp() * 1000)
    aggs = [SimpleNamespace(timestamp=ms(9, 29), open=1, high=2, low=0.5, close=1.5, volume=10),
            SimpleNamespace(timestamp=ms(9, 30), open=1, high=2, low=0.5, close=1.5, volume=10),
            SimpleNamespace(timestamp=ms(15, 59), open=1, high=2, low=0.5, close=1.5, volume=None),
            SimpleNamespace(timestamp=ms(16, 0), open=1, high=2, low=0.5, close=1.5, volume=10)]
    rows = ib.rth_rows(aggs, "QQQ", et, dt.time(15, 59))
    assert [r[1].time() for r in rows] == [dt.time(9, 30), dt.time(15, 59)]
    assert rows[0][2] == dt.date(2026, 9, 2) and rows[1][7] is None
    assert len(ib.rth_rows(aggs, "SPY", et, dt.time(15, 45))) == 1


def test_scope():
    src = inspect.getsource(ib)
    assert "list_aggs" in src and "ON CONFLICT (ticker, ts) DO NOTHING" in src
    # the live tables may be NAMED in the docstring (to say they are not
    # touched) but never written or read
    for forbidden in ("INTO paper_spec_bars", "INTO rsl_book_bars", "FROM paper_spec_bars",
                      "FROM rsl_book_bars", "paper_trades"):
        assert forbidden not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
