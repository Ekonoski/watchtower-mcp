"""The FVG snapshot: zones join the record, absence stays unambiguous.

2026-08-13: the gamma board shipped its Imbalances section as a declared
hole because detect_fvgs only ever ran per-request inside the service —
nothing persisted, so a session without the live engine had nothing to
read. The fix is the same one every other board input got: a morning
sweep into fvg_runs / fvg_zones from recorded daily bars.

What this file pins:
  1. The daily_prices -> bar-dict seam carries fields in the right order.
     paper_spec_bars once shipped a silent (close, high, low) swap; the
     same swap here would FABRICATE zones from real bars.
  2. The universe is gamma venues + watchlist + open positions, sorted,
     deduped — an open position without its imbalance map is flying blind.
  3. Zone rows carry every field the reading doctrine demands — direction,
     status, edges, age, and the per-row formation date.
  4. By signature: the writer takes no bars and no prices — it can only
     read the recorded tables (reconstruction is not tape).

Standalone per house convention:  python3 tests/test_fvg_snapshot.py
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.fvg import (  # noqa: E402
    _bar_dicts, _zone_rows, detect_fvgs, fvg_universe, write_fvg_snapshot,
)


def main():
    # 1. Field order across the seam: (trade_date, open, high, low, close).
    rows = [(dt.date(2026, 8, 11), 10.0, 12.0, 9.5, 11.0)]
    b = _bar_dicts(rows)[0]
    assert b == {"date": "2026-08-11", "open": 10.0, "high": 12.0,
                 "low": 9.5, "close": 11.0}, b

    # A real gap survives the seam end to end: candle 1 high 10, candle 3
    # low 12, displacement middle — detect_fvgs must see it from tuples.
    base = [(dt.date(2026, 8, d), 9.8, 10.0, 9.7, 9.9) for d in range(1, 9)]
    base += [(dt.date(2026, 8, 9), 9.8, 10.0, 9.6, 9.9),
             (dt.date(2026, 8, 10), 9.9, 11.9, 9.8, 11.8),   # displacement
             (dt.date(2026, 8, 11), 12.1, 12.6, 12.0, 12.5)]
    gaps = detect_fvgs(_bar_dicts(base))
    assert len(gaps) == 1 and gaps[0]["side"] == "bullish", gaps
    assert gaps[0]["bottom"] == 10.0 and gaps[0]["top"] == 12.0, gaps
    assert gaps[0]["formed"] == "2026-08-10", gaps

    # 2. Universe: venues + watchlist + held, sorted, deduped.
    u = fvg_universe(("SPY", "QQQ"), ["NVDA", "SPY"], ["COR", "NVDA"])
    assert u == ["COR", "NVDA", "QQQ", "SPY"], u

    # 3. Zone rows keep every doctrinal field, in table order.
    zr = _zone_rows(7, [{"side": "bullish", "status": "open", "top": 12.0,
                         "bottom": 10.0, "mid": 11.0, "age_bars": 1,
                         "formed": "2026-08-10", "inverted_on": None}])
    assert zr == [(7, "bullish", "open", 12.0, 10.0, 11.0, 1,
                   "2026-08-10", None)], zr

    # 4. The writer reads the record and nothing else — no bars, prices,
    #    or tickers can be injected past the recorded tables.
    assert list(inspect.signature(write_fvg_snapshot).parameters) == []

    print("ok — the seam carries fields straight, the universe covers "
          "venues+watchlist+held, zone rows keep their dates, and the "
          "writer can only read the record")


if __name__ == "__main__":
    main()
