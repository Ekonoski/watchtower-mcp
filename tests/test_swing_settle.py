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

from analysis.paper_trader import swing_loop_decision, swing_settle_decision  # noqa: E402

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

    # 2026-09-10: the LOOP's swing branch. At 15:55 its eod flag is set
    # and its newest completed bar is the 15:30–15:45 one — HBB closed
    # that bar at 32.74, a cent under its 32.75 stop, and the official
    # close printed 32.90. That bar is not the daily close; no stop.
    t1530 = dt.datetime(2026, 8, 21, 19, 30, tzinfo=UTC).astimezone(
        __import__("zoneinfo").ZoneInfo("America/New_York"))
    px, why = swing_loop_decision("long", 32.75, 45.98, t1530, 32.74, 32.80, 32.70,
                                  eod=True, post_entry=True)
    assert (px, why) == (None, None), (px, why)
    # The same close on the TRUE final bar would stop (the loop never
    # holds it completed; the settle owns it — but the rule is one rule).
    t1545 = t1530 + dt.timedelta(minutes=15)
    px, why = swing_loop_decision("long", 32.75, 45.98, t1545, 32.74, 32.80, 32.70,
                                  eod=True, post_entry=True)
    assert (px, why) == (32.74, "stop"), (px, why)
    # Targets still fill on a touch intraday, and pre-entry bars decide nothing.
    px, why = swing_loop_decision("long", 32.75, 45.98, t1530, 40.0, 46.0, 39.0,
                                  eod=False, post_entry=True)
    assert (px, why) == (45.98, "target"), (px, why)
    px, why = swing_loop_decision("long", 32.75, 45.98, t1530, 40.0, 46.0, 39.0,
                                  eod=False, post_entry=False)
    assert (px, why) == (None, None), (px, why)
    assert "conn" not in inspect.signature(swing_loop_decision).parameters

    # 2026-09-11, ACVA: entry 7.86, target 9.93, the 9:30 bar OPENED 10.455
    # on a +45% gap. The exit booked 9.93 — a price the tape never printed
    # (day low 10.30). A bar that opens beyond the target fills at its OPEN,
    # in the loop and in the settle alike; a touch still fills at the target.
    t0930 = dt.datetime(2026, 9, 11, 13, 30, tzinfo=UTC).astimezone(
        __import__("zoneinfo").ZoneInfo("America/New_York"))
    px, why = swing_loop_decision("long", 6.615, 9.93, t0930, 10.41, 10.46, 10.30,
                                  eod=False, post_entry=True, op=10.455)
    assert (px, why) == (10.455, "target"), (px, why)
    px, why = swing_loop_decision("long", 6.615, 9.93, t0930, 9.95, 10.10, 9.80,
                                  eod=False, post_entry=True, op=9.80)
    assert (px, why) == (9.93, "target"), (px, why)               # touched from below: the target
    px, why = swing_loop_decision("short", 12.0, 9.93, t0930, 9.50, 9.60, 9.40,
                                  eod=False, post_entry=True, op=9.55)
    assert (px, why) == (9.55, "target"), (px, why)               # the short mirror
    px, why = swing_settle_decision("long", 6.615, 9.93, _bar("1945", 10.455, 10.41, 10.46, 10.30))
    assert (px, why) == (10.455, "target"), (px, why)

    print("ok — the 15:45 bar that fooled the old convention stays a "
          "non-exit, the true close books AGMB's -1.07R, wicks never "
          "decide, target touch matches the loop, and the decision is "
          "structurally unable to fetch")


if __name__ == "__main__":
    main()
