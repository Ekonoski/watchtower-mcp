"""The swing true-close settle, pinned with AGMB's real 2026-08-14 bars.

The live loop's window ends at 15:58, so its "daily close" was really the
15:30–15:45 bar. AGMB closed that bar at 13.16 — nine-tenths of a cent
ABOVE its 13.1507 stop — then printed the true daily close at 13.03,
twelve cents through it, and no exit fired. Eric ruled (2026-08-15):
settle on the TRUE final bar, one rule for winners and losers alike, and
AGMB books the −1.07R. This pins the decision function on those bars and,
by signature, that it is pure — it cannot fetch, so a settle can only
ever read recorded tape.

Standalone per house convention:  python3 tests/test_swing_settle.py
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import swing_settle_decision  # noqa: E402

UTC = dt.timezone.utc


def _bar(hhmm_utc, op, close, hi, lo):
    h, m = int(hhmm_utc[:2]), int(hhmm_utc[2:])
    return (dt.datetime(2026, 8, 14, h, m, tzinfo=UTC), op, close, hi, lo)


def main():
    STOP, TGT = 13.1507, 18.605

    # The 15:30–15:45 bar the old convention judged: close 13.16, above
    # the stop — correctly no exit on THAT bar.
    px, why = swing_settle_decision("long", STOP, TGT, _bar("1930", 13.19, 13.16, 13.725, 13.16))
    assert (px, why) == (None, None), (px, why)

    # The true closing bar: 13.03, twelve cents through the stop — exits
    # at the close, wick rule intact (the 13.745 high is irrelevant).
    px, why = swing_settle_decision("long", STOP, TGT, _bar("1945", 13.27, 13.03, 13.745, 13.03))
    assert (px, why) == (13.03, "stop"), (px, why)
    # AGMB's ledger entry: entry 14.91, risk 1.7593 → −1.07R.
    assert round((13.03 - 14.91) / (14.91 - STOP), 2) == -1.07

    # A closing-bar wick THROUGH the stop with a close back above is not
    # an exit — completed closes decide, pokes never do.
    px, why = swing_settle_decision("long", STOP, TGT, _bar("1945", 13.2, 13.18, 13.30, 13.03))
    assert (px, why) == (None, None), (px, why)

    # Target on a touch, same as the live loop; stop-beyond-close wins
    # precedence when both print in one bar.
    px, why = swing_settle_decision("long", STOP, TGT, _bar("1945", 18.0, 18.4, 18.7, 17.9))
    assert (px, why) == (TGT, "target"), (px, why)
    px, why = swing_settle_decision("long", STOP, TGT, _bar("1945", 18.0, 13.05, 18.7, 13.0))
    assert (px, why) == (13.05, "stop"), (px, why)

    # Pure by signature: no connection — the settle can only read tape
    # someone already recorded.
    assert "conn" not in inspect.signature(swing_settle_decision).parameters

    print("ok — the 15:45 bar that fooled the old convention stays a "
          "non-exit, the true close books AGMB's -1.07R, wicks never "
          "decide, target touch matches the loop, and the decision is "
          "structurally unable to fetch")


if __name__ == "__main__":
    main()
