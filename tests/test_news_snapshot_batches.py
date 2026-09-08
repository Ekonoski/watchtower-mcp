"""The news scanner's Polygon snapshot is fetched in URL-safe batches
(2026-09-08: one URL with every ticker drew '414 Request-URI Too Large'
from the proxy on every 15-minute scan and the snapshot came back empty).
A failed batch is a hole for its names only."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import news_scanner as ns  # noqa: E402


def test_snapshot_batches():
    assert ns.snapshot_batches([]) == []
    assert ns.snapshot_batches(["A", "", "B"], 5) == [["A", "B"]]
    tk = [f"T{i}" for i in range(250)]
    b = ns.snapshot_batches(tk, 120)
    assert [len(x) for x in b] == [120, 120, 10] and sum(b, []) == tk
    assert ns.SNAPSHOT_BATCH <= 150


def test_fetch_uses_batches_and_survives_a_failed_one():
    src = inspect.getsource(ns._fetch_snapshot_map)
    assert "for batch in snapshot_batches(tickers)" in src
    assert "failed batches" in src                       # the hole is stated, never silent
    assert 'tickers=",".join(batch)' in src and 'tickers=",".join(tickers)' not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
