"""The binary-day shadow, pinned with 2026-08-12's real CPI numbers.

Decision (Eric, 2026-08-12): the binary gate keeps skipping the whole day —
but whether it over-pays for that protection is measured, not argued.
Every skipped_binary spec shadow-re-arms at 10:30 ET if the recorded 10:30
board still shows its level, then grades by the live gamma rules from
recorded bars. The shadow never places a trade and never touches spec
status.

What this file pins:
  1. The decision matches on the LEVEL, not the quantized setup name —
     2026-08-12's own QQQ flip drifted 716.65 -> 716.09 between the 7:30
     and 10:30 sweeps, which _qlvl's half-point grid calls two different
     names; the shadow must call it the same level. A real wall migration
     (775 -> 780, 0.65%) must NOT match.
  2. The decision goes THROUGH build_gamma_specs off the recorded board, so
     regime and every arming gate are re-applied by live code — the real
     2026-08-12 10:30 SPY/QQQ boards must re-arm all four skipped specs.
  3. The outcome replays the gamma fill rules: wick rule on entries and
     stops, no entries at/after the 14:30 no-new clock, eod_flat on the
     same bar the live 15:55 pass reads, R from the actual shadow entry —
     and a record that ends mid-trade stays OPEN (a hole, never a flat
     close). 2026-08-12's own headline: triggers never printed, entered_at
     None, the skip cost 0R stated from tape.

Standalone per house convention:  python3 tests/test_binary_shadow.py
"""
import datetime as dt
import inspect
import os
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import (  # noqa: E402
    build_gamma_specs, shadow_outcome, shadow_rearm_decision,
)

ET = zoneinfo.ZoneInfo("America/New_York")
D = dt.date(2026, 8, 12)


def bar(hh, mm, op, cl, hi, lo):
    return (dt.datetime(2026, 8, 12, hh, mm, tzinfo=ET), op, cl, hi, lo)


# The recorded 2026-08-12 10:30:06 gex_intraday rows, verbatim.
BOARD_1030 = [
    ("QQQ", 725.24, 730.0, 700.0, 716.09, 5.654, "pinning"),
    ("SPY", 772.77, 775.0, 770.0, 769.60, 6.947, "pinning"),
]
# The four skipped_binary specs the 7:40 writer produced that morning
# (ids 248-251): (ticker, setup, trigger).
MORNING = [
    ("QQQ", "wall_fade_730", 730.0),
    ("QQQ", "flip_hold_716.5", 716.65),
    ("SPY", "wall_fade_775", 775.0),
    ("SPY", "flip_hold_769", 768.95),
]


def main():
    live, _skips = build_gamma_specs(D, BOARD_1030, "shadow")

    # 2026-08-12, the fixture that motivated the feature: all four skipped
    # specs re-arm off the real 10:30 board — including the QQQ flip, whose
    # quantized name changed (flip_hold_716.5 vs the board's flip_hold_716)
    # while the level moved 0.08%.
    for tk, setup, trig in MORNING:
        rearmed, reason = shadow_rearm_decision(tk, setup, trig, live)
        assert rearmed is True, (tk, setup, reason)
    # A ticker can never match another ticker's level.
    rearmed, _ = shadow_rearm_decision("IWM", "wall_fade_775", 775.0, live)
    assert rearmed is False

    # A real wall migration is a different trade: same board but with SPY's
    # call wall walked to 780 — the 775 fade must NOT re-arm (0.65% is a
    # move, not a wobble), and the reason must say so.
    board_moved = [BOARD_1030[0],
                   ("SPY", 772.77, 780.0, 770.0, 769.60, 6.947, "pinning")]
    live_moved, _ = build_gamma_specs(D, board_moved, "shadow")
    rearmed, reason = shadow_rearm_decision("SPY", "wall_fade_775", 775.0,
                                            live_moved)
    assert rearmed is False, reason
    assert "absent" in reason

    # Regime flip kills the setup through live code, not a special case:
    # slippery emits no wall_fade, so the fade finds no level to match.
    board_slip = [("QQQ", 715.0, 730.0, 700.0, 716.09, 5.654, "slippery")]
    live_slip, _ = build_gamma_specs(D, board_slip, "shadow")
    rearmed, _ = shadow_rearm_decision("QQQ", "wall_fade_730", 730.0,
                                       live_slip)
    assert rearmed is False

    # ── outcome: 2026-08-12's own headline — the trigger never printed ──
    # QQQ wall fade at 730 against a tape that never left the 724-726 band:
    # no touch, no fill, the skip cost 0R and the record SAYS so.
    quiet = [bar(10, 30, 725.2, 725.5, 726.3, 724.1),
             bar(10, 45, 725.5, 724.9, 725.8, 724.0)]
    out = shadow_outcome("short", 730.0, 731.09, 716.09, quiet)
    assert out["entered_at"] is None and out["r_multiple"] is None, out

    # Touch + same-bar close back under = entry at that bar's close (the
    # wick through the wall is not an entry; the close back under is), then
    # a target touch exits at the target, R from the ACTUAL entry.
    run = [bar(10, 30, 729.0, 729.4, 730.2, 728.8),    # touches 730, closes under
           bar(10, 45, 729.4, 725.0, 729.6, 724.9),
           bar(11, 0, 725.0, 717.0, 725.2, 716.0)]     # low 716.0 <= tgt 716.09
    out = shadow_outcome("short", 730.0, 731.09, 716.09, run)
    assert out["entry_px"] == 729.4 and out["exit_reason"] == "target", out
    assert out["exit_px"] == 716.09, out
    r = round((729.4 - 716.09) / (731.09 - 729.4), 2)
    assert out["r_multiple"] == r, (out, r)

    # Wick rule on the stop: a spike through 731.09 that closes back under
    # is nothing; the stop is a CLOSE beyond, at that close.
    stopped = [bar(10, 30, 729.0, 729.4, 730.2, 728.8),
               bar(10, 45, 729.4, 730.8, 731.5, 729.2),   # wick past stop, close under
               bar(11, 0, 730.8, 731.4, 731.6, 730.5)]    # CLOSE past stop
    out = shadow_outcome("short", 730.0, 731.09, 716.09, stopped)
    assert out["exit_reason"] == "stop" and out["exit_px"] == 731.4, out
    assert out["r_multiple"] < 0, out

    # The 14:30 no-new clock binds the shadow too: a textbook touch-and-
    # close on the bar ending 14:30 must not enter (the live pass at 14:30
    # already refuses new entries).
    late = [bar(14, 15, 729.0, 729.4, 730.2, 728.8)]
    out = shadow_outcome("short", 730.0, 731.09, 716.09, late)
    assert out["entered_at"] is None, out

    # eod_flat lands on the bar the live 15:55 pass reads — the 15:30-start
    # bar's close — and only when the record proves the day got there.
    day = [bar(10, 30, 729.0, 729.4, 730.2, 728.8),
           bar(15, 30, 728.0, 727.5, 728.2, 727.0),
           bar(15, 45, 727.5, 726.0, 727.6, 725.9)]   # ends 16:00 — never decides
    out = shadow_outcome("short", 730.0, 731.09, 716.09, day)
    assert out["exit_reason"] == "eod_flat" and out["exit_px"] == 727.5, out

    # Record ends mid-trade (deploy gap): the trade stays OPEN — a hole in
    # the record renders as one, never as a flat close at whatever bar
    # happened to be last.
    cut = [bar(10, 30, 729.0, 729.4, 730.2, 728.8),
           bar(10, 45, 729.4, 728.0, 729.5, 727.8)]
    out = shadow_outcome("short", 730.0, 731.09, 716.09, cut)
    assert out["entered_at"] is not None and out["exited_at"] is None, out

    # The shadow is measurement only: pure functions with no connection in
    # their signatures, so they CANNOT arm, fill, or cancel anything live —
    # the same by-signature blindness test_cipher_tag pins for the tag.
    assert "conn" not in inspect.signature(shadow_rearm_decision).parameters
    assert "conn" not in inspect.signature(shadow_outcome).parameters

    print("ok — skipped specs shadow-re-arm off the recorded 10:30 board "
          "(level, not name), grade by the live gamma rules from tape, and "
          "2026-08-12's skip is pinned at 0R: no trigger ever printed")


if __name__ == "__main__":
    main()
