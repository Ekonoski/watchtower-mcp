"""The seam that silently killed the trigger loop, pinned.

2026-08-07, the paper desk's first live day: 151 swing limits armed, ~58
triggers touched intraday, ZERO fills and ZERO end-of-day cancels. Cause:
`fetch_recent_bars` returned bars keyed `date`/`open`/... while
`_last_closed_15m` read `b.get("timestamp")` — always None, so every ticker
returned no bars and every spec hit `continue`. No test crossed the seam
between the two modules, so a loop that could never fire failed nothing
(the `_social_block` lesson, again, in the money path this time).

This test feeds `_last_closed_15m` bars shaped EXACTLY like
`fetch_recent_bars` builds them — if either side of the contract drifts,
this fails loudly.

Standalone per house convention:  python3 tests/test_paper_bars_contract.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import paper_trader  # noqa: E402


def _bar_like_fetch_recent_bars(ts_ms: int, o: float, h: float, l: float, c: float):
    """Mirror of the dict polygon_data.fetch_recent_bars constructs.
    Keep in sync with that function — drifting keys is the whole bug."""
    return {
        "date": dt.date.fromtimestamp(ts_ms / 1000).isoformat(),
        "timestamp": ts_ms,
        "open": o, "high": h, "low": l, "close": c,
        "volume": 1000, "vwap": None,
    }


def main():
    now = dt.datetime.now(dt.timezone.utc)

    def fake_fetch(tk, days=2, multiplier=15, timespan="minute"):
        # three completed 15m bars ending 60/45/30 minutes ago (today), plus
        # one still-open bar that must be filtered out
        out = []
        for mins_ago in (75, 60, 45):
            start = now - dt.timedelta(minutes=mins_ago)
            out.append(_bar_like_fetch_recent_bars(
                int(start.timestamp() * 1000), 10.0, 10.5, 9.8, 10.2))
        live = now - dt.timedelta(minutes=5)   # open bar: not yet complete
        out.append(_bar_like_fetch_recent_bars(
            int(live.timestamp() * 1000), 10.2, 10.6, 10.1, 10.4))
        return out

    orig = paper_trader.fetch_recent_bars
    paper_trader.fetch_recent_bars = fake_fetch
    try:
        bars = paper_trader._last_closed_15m("TEST")
    finally:
        paper_trader.fetch_recent_bars = orig

    assert bars, ("_last_closed_15m returned no bars from correctly-shaped "
                  "input — the fetch/parse contract is broken again")
    assert len(bars) == 3, f"expected 3 completed bars (open bar filtered), got {len(bars)}"
    ts, op, close, hi, lo, vol = bars[-1]
    assert (op, close, hi, lo) == (10.0, 10.2, 10.5, 9.8), f"bar fields mis-mapped: {bars[-1]}"
    # 2026-08-21: bars carry volume (6th field) for the defense shadow —
    # mapped straight from the fetch payload, holes stay None.
    assert vol == 1000.0, f"volume mis-mapped: {vol!r}"
    print(f"ok — {len(bars)} completed bars parsed, open bar filtered, fields mapped")


if __name__ == "__main__":
    main()


def test_no_fixed_width_bar_unpacks_survive_in_paper_trader():
    """2026-08-24: `ts, op_, close, hi, lo = bars[-1]` — one five-way
    unpack of the six-element bar tuple survived Friday's star-tolerance
    sweep and took the trigger loop down at Monday's open (no RTH bars,
    no fills, stops unwatched). Every destructuring of a bar tuple must
    be star-tolerant so the NEXT added element degrades gracefully."""
    import re
    import inspect
    from analysis import paper_trader
    src = inspect.getsource(paper_trader)
    fixed = [ln.strip() for ln in src.splitlines()
             if re.search(r"=\s*bars\[-?\d+\]\s*$", ln)
             and "*" not in ln.split("=")[0]
             and re.search(r",.*,", ln.split("=")[0])]
    assert not fixed, f"fixed-width bar unpack(s): {fixed}"
