"""The bar-persistence seam, pinned before it can bite.

paper_spec_bars exists because reconstruction is not tape (TNDM,
2026-08-08: a fabricated 18.60 close reached an audit card labeled
"real"). The loop persists every 15m bar it evaluates; audits replay from
the record. This test pins the field mapping across the seam —
_last_closed_15m tuples are (ts, open, CLOSE, high, low) while the table
is (open, high, low, close), and a silent swap here is the same class of
bug that no-op'd the trigger loop on day one.

Standalone per house convention:  python3 tests/test_spec_bars_persist.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import _spec_bar_rows  # noqa: E402


def main():
    ts = dt.datetime(2026, 8, 7, 9, 45, tzinfo=dt.timezone.utc)
    d = dt.date(2026, 8, 7)
    # A bar where every field differs — any mis-mapping changes the row.
    # (ts, open, close, high, low) per _last_closed_15m.
    rows = _spec_bar_rows("TNDM", d, [(ts, 17.85, 18.60, 18.75, 17.80)])
    # 2026-08-21: rows carry volume before trade_date (None = legacy hole)
    assert rows == [("TNDM", ts, 17.85, 18.75, 17.80, 18.60, None, d)], rows

    # high must be the max and low the min of the stored row's prices —
    # a swapped mapping fails this even on bars with plausible shapes.
    _tk, _ts, o, h, l, c, _v, _d = rows[0]
    assert h == max(o, h, l, c) and l == min(o, h, l, c), rows

    assert _spec_bar_rows("X", d, []) == []

    print("ok — (ts, open, close, high, low) maps to "
          "(ticker, ts, open, high, low, close, trade_date), empty stays empty")


if __name__ == "__main__":
    main()
