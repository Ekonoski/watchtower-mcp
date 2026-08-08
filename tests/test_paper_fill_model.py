"""The honest fill model, pinned with 2026-08-07's real numbers.

The shadow audit found phantom fills on BOTH sides of the ledger: ARW
"filled" at its 220.87 trigger on a day whose HIGH was 209.60 (phantom
loss booked at a price that never traded), and TNDM "filled" at 18.16
after opening 4.2% below it (real limit fills at the 17.39 open — the
win was understated AND, under an attended convention, arguable). The
fix: resting-limit mechanics with a dead-on-arrival guard, one rule for
winners and losers alike.

Provenance (corrected 2026-08-08, the TNDM lesson: reconstruction is not
tape): the DAILY facts here are verified — ARW's 209.60 open below its
stop, BLND's 1.72 high never reaching 1.88, TNDM's 17.39 open 4.2%% below
its trigger. The intraday 15m SEQUENCES are illustrative of the mechanics;
an earlier version labeled TNDM's 18.60 confirming bar "actual" and chart
verification found no such close printed. From 2026-08-10 the loop
persists every bar it evaluates (paper_spec_bars), so future cases pin
recorded tape, not vendor archaeology.

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
    verdict, px, kind = _swing_fill("long", 26.65, 25.56,
                              [bar(26.90, 26.80, 26.95, 26.70),
                               bar(26.78, 27.00, 27.05, 26.65, 15)])
    assert (verdict, px, kind) == ("fill", 26.65, "touch"), (verdict, px, kind)

    # TNDM-shaped (real daily facts, illustrative 15m sequence): opened
    # 17.39, 4.2% below the 18.16 trigger (stop 16.14). Level lost ->
    # RECLAIM mode. Proof = a completed 15m bar CLOSING back through the
    # trigger (Eric's rule, 2026-08-08: a wick is not proof). The gap bar
    # does not fill; a bar closing 18.60 fills at ITS CLOSE — the premium
    # over 18.16 is the cost of confirmation. (TNDM's true confirming
    # close is pending verification against recorded tape.)
    verdict, px, kind = _swing_fill("long", 18.16, 16.14,
                              [bar(17.39, 17.80, 17.90, 17.07)])
    assert (verdict, px, kind) == (None, None, None), (verdict, px, kind)
    verdict, px, kind = _swing_fill("long", 18.16, 16.14,
                              [bar(17.39, 17.80, 17.90, 17.07),
                               bar(17.85, 18.60, 18.75, 17.80, 15)])
    assert (verdict, px, kind) == ("fill", 18.60, "reclaim"), (verdict, px, kind)

    # The wick fakeout this rule exists to refuse: after the level is
    # lost, a bar SPIKES through 18.16 (high 18.30) but closes back below
    # at 17.85. Cross-fill would have bought the top of a failed reclaim;
    # close-through fills nothing.
    verdict, px, kind = _swing_fill("long", 18.16, 16.14,
                              [bar(17.39, 17.80, 17.90, 17.07),
                               bar(17.90, 17.85, 18.30, 17.60, 15)])
    assert (verdict, px, kind) == (None, None, None), (verdict, px, kind)

    # Once lost, reclaim mode is permanent: a later bar that gaps back
    # ABOVE the level and closes there is still proof — fills at its
    # close, however price got back over.
    verdict, px, kind = _swing_fill("long", 100.0, 95.0,
                              [bar(98.0, 99.0, 99.5, 97.5),
                               bar(101.0, 102.0, 102.5, 100.8, 15)])
    assert (verdict, px, kind) == ("fill", 102.0, "reclaim"), (verdict, px, kind)

    # BLND, Friday: opened below the 1.88 trigger (above the 1.67 stop) and
    # NEVER reclaimed — high 1.72. No proof, no fill; the spec rests and
    # cancels at end of day upstream. (The blind model owned this loser.)
    verdict, px, kind = _swing_fill("long", 1.88, 1.67,
                              [bar(1.71, 1.63, 1.72, 1.62)])
    assert (verdict, px, kind) == (None, None, None), (verdict, px, kind)

    # ARW, Friday: opened 209.60 — below the 211.655 STOP of its 220.87
    # trigger. First marketable price is beyond the stop → dead on arrival,
    # cancelled, never entered. Nobody knowingly enters an already-stopped
    # trade. (The old blind-limit logic booked this at 220.87: -1.88R of
    # pure fiction.)
    verdict, px, kind = _swing_fill("long", 220.87, 211.655,
                              [bar(209.60, 205.00, 209.60, 200.00)])
    assert (verdict, px, kind) == ("doa", None, None), (verdict, px, kind)

    # No touch at all: price stays above the trigger → no fill, spec keeps
    # resting (and cancels at end of day upstream).
    verdict, px, kind = _swing_fill("long", 100.0, 95.0,
                              [bar(103.0, 104.0, 104.5, 101.5)])
    assert (verdict, px, kind) == (None, None, None), (verdict, px, kind)

    # Short mirrors: trigger 50, stop 53. Opens at 54 — beyond the stop —
    # DOA. Opens at 51 (level lost for a short, still inside the stop) and
    # trades back DOWN through 50 → reclaim fills at the trigger.
    verdict, px, kind = _swing_fill("short", 50.0, 53.0,
                              [bar(54.0, 53.5, 54.2, 52.8)])
    assert (verdict, px, kind) == ("doa", None, None), (verdict, px, kind)
    verdict, px, kind = _swing_fill("short", 50.0, 53.0,
                              [bar(51.0, 49.8, 51.4, 49.6)])
    assert (verdict, px, kind) == ("fill", 49.8, "reclaim"), (verdict, px, kind)
    # short wick-fake: dips through 50 (low 49.9) but closes back above.
    verdict, px, kind = _swing_fill("short", 50.0, 53.0,
                              [bar(51.0, 50.2, 51.4, 49.9)])
    assert (verdict, px, kind) == (None, None, None), (verdict, px, kind)

    print("ok — retest fills at trigger (kind=touch), lost level needs a 15m "
          "CLOSE back through (kind=reclaim), wick-fakes refused, DOA "
          "cancels, shorts mirror")


if __name__ == "__main__":
    main()
