"""The honest fill model, pinned with 2026-08-07's real numbers.

The shadow audit found phantom fills on BOTH sides of the ledger: ARW
"filled" at its 220.87 trigger on a day whose HIGH was 209.60 (phantom
loss booked at a price that never traded), and TNDM "filled" at 18.16
after opening 4.2% below it (real limit fills at the 17.39 open — the
win was understated AND, under an attended convention, arguable). The
fix: resting-limit mechanics with a dead-on-arrival guard, one rule for
winners and losers alike. These cases use the actual Friday bars.

Standalone per house convention:  python3 tests/test_paper_fill_model.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import _swing_fill  # noqa: E402

T = dt.datetime(2026, 8, 7, 9, 45, tzinfo=dt.timezone.utc)


def bar(op, cl, hi, lo, mins=0):
    return (T + dt.timedelta(minutes=mins), op, cl, hi, lo)


def main():
    # CAE, Friday: opened above its 26.65 trigger, dipped to touch it
    # exactly, closed higher. Clean retest from above → fill AT the trigger.
    verdict, px = _swing_fill("long", 26.65, 25.56,
                              [bar(26.90, 26.80, 26.95, 26.70),
                               bar(26.78, 27.00, 27.05, 26.65, 15)])
    assert (verdict, px) == ("fill", 26.65), (verdict, px)

    # TNDM, Friday: opened 17.39, 4.2% below the 18.16 trigger (stop 16.14),
    # then ripped. Open is above the stop → real limit fills AT THE OPEN,
    # not the trigger. Booking 18.16 here was the winning-side phantom.
    verdict, px = _swing_fill("long", 18.16, 16.14,
                              [bar(17.39, 17.80, 17.90, 17.07)])
    assert (verdict, px) == ("fill", 17.39), (verdict, px)

    # ARW, Friday: opened 209.60 — below the 211.655 STOP of its 220.87
    # trigger. First marketable price is beyond the stop → dead on arrival,
    # cancelled, never entered. Nobody knowingly enters an already-stopped
    # trade. (The old blind-limit logic booked this at 220.87: -1.88R of
    # pure fiction.)
    verdict, px = _swing_fill("long", 220.87, 211.655,
                              [bar(209.60, 205.00, 209.60, 200.00)])
    assert (verdict, px) == ("doa", None), (verdict, px)

    # No touch at all: price stays above the trigger → no fill, spec keeps
    # resting (and cancels at end of day upstream).
    verdict, px = _swing_fill("long", 100.0, 95.0,
                              [bar(103.0, 104.0, 104.5, 101.5)])
    assert (verdict, px) == (None, None), (verdict, px)

    # Short mirror: sell-stop-style limit at 50 with stop 53. Bar opens at
    # 54 — beyond the stop — DOA. Opens at 51 and trades up through 50?
    # marketable (hi >= trig), fill at max(open, trig) = 51... above the
    # trigger is a BETTER short fill; still below the 53 stop → legit.
    verdict, px = _swing_fill("short", 50.0, 53.0,
                              [bar(54.0, 53.5, 54.2, 52.8)])
    assert (verdict, px) == ("doa", None), (verdict, px)
    verdict, px = _swing_fill("short", 50.0, 53.0,
                              [bar(51.0, 50.2, 51.4, 49.9)])
    assert (verdict, px) == ("fill", 51.0), (verdict, px)

    print("ok — clean touch fills at trigger, gap-open fills at open, "
          "DOA cancels, shorts mirror")


if __name__ == "__main__":
    main()
