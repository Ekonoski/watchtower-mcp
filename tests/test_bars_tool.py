"""watchtower_bars (2026-09-04), pinned: the formatter states the day's
OHLC and the 9:30 bar, hides nothing silently (hidden count stated),
renders zero bars as a hole, and the module writes nothing.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import bars_tool as bt  # noqa: E402


def test_format_states_open_930_and_hidden_count():
    t0 = dt.datetime(2026, 9, 4, 9, 30)
    bars = [(t0 + dt.timedelta(minutes=i), 100 + i * 0.1, 100.2 + i * 0.1,
             99.9 + i * 0.1, 100.1 + i * 0.1, 1000.0) for i in range(20)]
    out = bt.format_bars("SPY", dt.date(2026, 9, 4), "1m", bars, last_n=5)
    assert "9:30 bar: O 100.00" in out and "Open 100.00" in out
    assert "20 bars through 09:49" in out
    assert "15 earlier bars not shown" in out
    assert out.count("\n09:") == 5
    hole = bt.format_bars("SPY", dt.date(2026, 9, 4), "1m", [], note="fetch failed")
    assert "none available" in hole and "hole" in hole


def test_read_only_and_timeframes():
    src = inspect.getsource(bt)
    assert "INSERT" not in src and "UPDATE " not in src and "DELETE" not in src
    assert bt.MULT == {"1m": 1, "5m": 5, "15m": 15}
    assert "timeframe must be" in bt.bars_report("SPY", "2h")
    assert bt.bars_report("", "1m") == "ticker is required"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
