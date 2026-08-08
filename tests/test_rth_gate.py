"""The regular-session gate, pinned with 2026-08-07's recorded tape.

Decided 2026-08-08 (Eric): premarket moves are low-volume fakeouts — the
desk waits for open-market volume. Bars are persisted whenever seen, but
only bars living inside the regular session (start ≥ 9:30 ET, completing
by 16:00) decide fills, stops, or shadows.

Both cases below are real bars from paper_spec_bars (the backfilled
recorded tape, not reconstruction):

- MOS: the 9:15–9:30 premarket bar dipped to 23.26, touching the 23.3951
  limit. With premarket counted that books a −0.17R touch fill; gated to
  the regular session, the 9:30 bar opened through the level and no 15m
  bar ever closed back above (day high 23.405, a one-cent wick over) — no
  fill. The gate turns a premarket-fakeout loser into a correct refusal.
- TNDM: the gate changes nothing — the 9:30 bar (in-session) opened 17.39,
  lost the level, and closed 19.62; same-bar reclaim, entry at its close.

Standalone per house convention:  python3 tests/test_rth_gate.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import _rth, _swing_fill  # noqa: E402

ET = dt.timezone(dt.timedelta(hours=-4))


def bar(hh, mm, op, cl, hi, lo):
    return (dt.datetime(2026, 8, 7, hh, mm, tzinfo=ET), op, cl, hi, lo)


# Recorded 2026-08-07 tape (paper_spec_bars), abbreviated to the bars that
# decide each verdict.
MOS = [bar(9, 15, 23.51, 23.26, 23.51, 23.26),      # premarket touch of 23.3951
       bar(9, 30, 23.27, 23.135, 23.314, 22.99),    # open through the level
       bar(10, 15, 22.89, 23.16, 23.165, 22.875),
       bar(11, 0, 23.28, 23.34, 23.405, 23.24),     # day-high wick, closes under
       bar(15, 45, 22.995, 23.065, 23.105, 22.941)]
TNDM = [bar(8, 0, 17.85, 17.59, 18.05, 17.59),      # premarket, below trigger
        bar(9, 15, 18.09, 17.17, 18.64, 17.17),     # premarket wick over 18.16
        bar(9, 30, 17.39, 19.62, 19.69, 17.07),     # lost AND reclaimed in-session
        bar(9, 45, 19.58, 19.9, 20.42, 19.4)]


def main():
    # The gate itself: premarket and after-hours bars drop, session bars stay.
    kept = _rth(MOS)
    assert [b[0].time() for b in kept] == [dt.time(9, 30), dt.time(10, 15),
                                           dt.time(11, 0), dt.time(15, 45)], kept

    # MOS ungated: the premarket dip touches the resting limit — a fill the
    # desk no longer takes.
    verdict, px, kind = _swing_fill("long", 23.3951, 21.44, MOS)
    assert (verdict, px, kind) == ("fill", 23.3951, "touch"), (verdict, px, kind)
    # MOS gated: level lost at the 9:30 open, the 11:00 wick to 23.405 is
    # not a close — no fill, correctly refused.
    verdict, px, kind = _swing_fill("long", 23.3951, 21.44, _rth(MOS))
    assert (verdict, px, kind) == (None, None, None), (verdict, px, kind)

    # TNDM gated: 9:30 bar opens 17.39 (level lost, above the 16.14 stop),
    # closes 19.62 — same-bar reclaim, entry at the close. The 9:15
    # premarket wick to 18.64 decides nothing.
    verdict, px, kind = _swing_fill("long", 18.16, 16.14, _rth(TNDM))
    assert (verdict, px, kind) == ("fill", 19.62, "reclaim"), (verdict, px, kind)

    print("ok — premarket bars persist but never decide: MOS's fakeout touch "
          "refused, TNDM's in-session reclaim fills at 19.62")


if __name__ == "__main__":
    main()
