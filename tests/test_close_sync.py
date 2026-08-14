"""Close sync — the day's bars from the real-time source, pinned.

Eric, 2026-08-14: "our data is realtime. that shouldn't be an issue ever
again." The evening screens were reading yesterday because daily_prices
waited on the 10 PM batch; the 4:35 close sync upserts the session's
grouped-daily bars from Polygon. This pins the row shaping: unknown
tickers never enter the table (the universe belongs to the ingestion
pipeline), close-less bars are dropped, and known bars land intact.

Standalone per house convention:  python3 tests/test_close_sync.py
"""
import datetime as dt
import os
import sys
from types import SimpleNamespace as Agg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.close_sync import _sync_rows  # noqa: E402


def main():
    day = dt.date(2026, 8, 14)
    aggs = [
        Agg(ticker="UNH", open=400.1, high=403.0, low=398.5, close=401.73,
            volume=3_470_000),
        Agg(ticker="ZZZZZJUNK", open=1.0, high=1.1, low=0.9, close=1.05,
            volume=100),                       # not in the desk's universe
        Agg(ticker="AGX", open=None, high=None, low=None, close=None,
            volume=0),                         # close-less bar: dropped
    ]
    rows = _sync_rows(aggs, known={"UNH", "AGX"}, day=day)
    assert rows == [("UNH", day, 400.1, 403.0, 398.5, 401.73, 3_470_000)], rows

    # Empty inputs are empty outputs, never an error.
    assert _sync_rows([], {"UNH"}, day) == []
    assert _sync_rows(None, {"UNH"}, day) == []

    print("ok — known bars land intact, unknown tickers never enter the "
          "table, close-less bars drop, and empty is empty")


if __name__ == "__main__":
    main()
