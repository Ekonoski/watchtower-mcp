"""The bearish-structure warning, pinned with 2026-08-13's real CIFR rows.

The swing writer queries direction='bullish' only, so on 2026-08-13 it
armed CIFR's 83-score daily inverse_hs blind to two live bearish
structures the same scanner held on the same ticker: a weekly hs_top
(forming, trigger 17.08) and a daily lower_high (at RETEST, trigger
17.442). Doctrine already made breakdown detections warnings on held
longs; this closes the arming-time blind spot. The warning stamps into
the spec's rationale — the ledger carries it — and the morning log WARNs.

It must never gate: shorts are retired, and a tiebreaker is a gate in
disguise (the cipher-tag rule, applied again).

Standalone per house convention:  python3 tests/test_bearish_warning.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import bearish_conflicts  # noqa: E402


def main():
    # CIFR's actual 2026-08-13 rows, verbatim from pattern_scan.
    rows = [
        ("CIFR", "weekly", "hs_top", "forming", 17.08),
        ("CIFR", "daily", "lower_high", "retest", 17.442),
    ]
    w = bearish_conflicts(rows)
    assert set(w) == {"CIFR"}, w
    assert w["CIFR"] == ("hs_top weekly forming (trig 17.08) + "
                         "lower_high daily retest (trig 17.442)"), w["CIFR"]

    # Multiple tickers stay separate; one structure reads clean.
    w = bearish_conflicts(rows + [("RMD", "daily", "bear_flag", "forming", 220.0)])
    assert w["RMD"] == "bear_flag daily forming (trig 220)", w
    assert "CIFR" in w and len(w) == 2, w

    # No bearish rows -> no warnings, and the writer arms exactly as before.
    assert bearish_conflicts([]) == {}

    # Measurement only, by signature: pure, no connection, returns strings —
    # it CANNOT cancel, skip, or reorder a spec (the cipher-tag blindness
    # rule, applied to warnings).
    assert "conn" not in inspect.signature(bearish_conflicts).parameters

    print("ok — CIFR's two live bearish structures render as one stamped "
          "warning, multi-ticker maps stay separate, empty is empty, and "
          "the warning is structurally unable to gate")


if __name__ == "__main__":
    main()
