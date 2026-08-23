"""The day-bias intraday backfill, pinned (2026-08-23).

  1. RTH filter: only 9:30-15:45 ET bar-starts persist (premarket is
     excluded from decisions everywhere on this desk).
  2. Research-only by signature: no writes to paper tables.
  3. Idempotent by construction: ON CONFLICT DO NOTHING on the PK.
"""
import datetime as dt
import inspect
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.daybias_bars import TICKERS, _rth_rows  # noqa: E402

ET = ZoneInfo("America/New_York")


class _Agg:
    def __init__(self, ts_et, o=1.0, h=2.0, low=0.5, c=1.5, v=100.0):
        self.timestamp = int(ts_et.timestamp() * 1000)
        self.open, self.high, self.low, self.close = o, h, low, c
        self.volume = v


def _at(h, m):
    return dt.datetime(2026, 8, 21, h, m, tzinfo=ET)


def test_rth_filter_and_row_shape():
    aggs = [_Agg(_at(9, 15)),    # premarket — dropped
            _Agg(_at(9, 30)),    # first RTH bar — kept
            _Agg(_at(15, 45)),   # last RTH bar — kept
            _Agg(_at(16, 0))]    # post — dropped
    rows = _rth_rows(aggs, "SPY")
    assert len(rows) == 2
    tk, ts, tdate, o, h, low, c, v = rows[0]
    assert (tk, tdate) == ("SPY", dt.date(2026, 8, 21))
    assert ts.astimezone(ET).time() == dt.time(9, 30)
    assert (o, h, low, c, v) == (1.0, 2.0, 0.5, 1.5, 100.0)


def test_missing_volume_is_none_not_zero():
    a = _Agg(_at(10, 0))
    a.volume = None
    assert _rth_rows([a], "QQQ")[0][7] is None


def test_research_only_and_idempotent():
    from analysis import daybias_bars
    src = inspect.getsource(daybias_bars)
    for forbidden in ("INSERT INTO paper", "UPDATE paper"):
        assert forbidden not in src, forbidden
    assert "ON CONFLICT (ticker, ts) DO NOTHING" in src
    assert TICKERS == ("SPY", "QQQ", "IWM")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
