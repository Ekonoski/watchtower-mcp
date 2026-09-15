"""Polygon's `limit` counts BASE aggregates — the timespan unit — not the bars
returned (2026-09-15). A 30-minute request is minute-based, so 130 days of it
on a liquid name overran the 50,000 cap and `get_aggs` handed back the first
page only: MSFT's 4h feed ended 07-28, AAPL's 08-03, META's 08-07, and the 4h
pattern board had carried no mag-7 rows because every one of them failed the
freshness gate as "stale feed" (thin names came back whole, which is why it
read as a per-ticker vendor hole). Pinned: every multi-day fetch in the
shared helpers and the oscillator goes through `list_aggs`, which follows
`next_url`; and the session-anchored 4h builder consumes a paginated
iterator end to end (a second page reaches the last bucket).

Standalone per house convention:  python3 tests/test_polygon_pagination.py
"""
import datetime as dt
import inspect
import os
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import polygon_data as pdm  # noqa: E402
from analysis import oscillator as osc  # noqa: E402

ET = ZoneInfo("America/New_York")


def _agg(day, h, m, px):
    ts = int(dt.datetime(2026, 9, day, h, m, tzinfo=ET).timestamp() * 1000)
    return SimpleNamespace(timestamp=ts, open=px, high=px + 1, low=px - 1, close=px + 0.5,
                           volume=100, vwap=px)


class _TwoPageClient:
    """A client whose single-page read stops mid-history and whose paginated
    read reaches the end — the shape of the defect."""
    def __init__(self):
        self.page1 = [_agg(11, 9, 30, 10), _agg(11, 10, 0, 11), _agg(11, 13, 30, 12)]
        self.page2 = [_agg(14, 9, 30, 20), _agg(14, 13, 30, 21), _agg(14, 15, 30, 22)]
        self.calls = []

    def get_aggs(self, *a, **k):
        self.calls.append("get_aggs")
        return list(self.page1)

    def list_aggs(self, *a, **k):
        self.calls.append("list_aggs")
        for x in self.page1:
            yield x
        for x in self.page2:
            yield x


def test_session_4h_builder_reads_every_page(monkeypatch=None):
    client = _TwoPageClient()
    orig = pdm.get_client
    pdm.get_client = lambda: client
    try:
        bars = pdm.fetch_session_4h_bars("MSFT", days=120)
    finally:
        pdm.get_client = orig
    assert client.calls == ["list_aggs"], client.calls
    # both sessions of both days, 9:30 and 13:30 buckets, last bucket present
    assert [(b["date"], b["session"]) for b in bars] == [
        ("2026-09-11", "09:30"), ("2026-09-11", "13:30"),
        ("2026-09-14", "09:30"), ("2026-09-14", "13:30")]
    assert bars[-1]["close"] == 22.5 and bars[-1]["high"] == 23


def test_recent_bars_reads_every_page():
    client = _TwoPageClient()
    orig = pdm.get_client
    pdm.get_client = lambda: client
    try:
        bars = pdm.fetch_recent_bars("MSFT", days=120, multiplier=30, timespan="minute")
    finally:
        pdm.get_client = orig
    assert client.calls == ["list_aggs"]
    assert len(bars) == 6 and bars[-1]["date"] == "2026-09-14"
    assert bars[-1]["timestamp"] == client.page2[-1].timestamp     # the paper trader's seam


def test_every_multi_day_fetch_paginates():
    for fn in (pdm.fetch_recent_bars, pdm.fetch_session_4h_bars,
               osc.fetch_intraday_confirmed, osc.fetch_daily_long):
        src = inspect.getsource(fn)
        assert "list_aggs(" in src and "get_aggs(" not in src, fn.__name__
    assert "BASE-AGGREGATE LIMIT" in (pdm.__doc__ or "")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
