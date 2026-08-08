"""The swing book's class gate, pinned.

2026-08-08: the book expanded from the three neckline families to every
(pattern, timeframe) class with a positive backtest prior. The trap this
test exists for: the SQL query filters pattern and timeframe INDEPENDENTLY,
so ema_bounce daily — the worst class on the board (-0.37R avg, n=162) —
passes both individual filters and only the joint class gate keeps it out.
A future "simplification" back to flat lists would quietly re-admit it and
fail no test but this one.

Standalone per house convention:  python3 tests/test_swing_classes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import (  # noqa: E402
    SWING_CLASSES, SWING_PATTERNS, swing_class_ok)


def main():
    # The additions (2026-08-08), strongest priors on the board:
    assert swing_class_ok("asc_triangle", "weekly")    # +2.61R, n=51
    assert swing_class_ok("ema_bounce", "weekly")      # +0.98R, n=205
    assert swing_class_ok("bull_flag", "weekly")       # +0.39R, n=44
    assert swing_class_ok("asc_triangle", "daily")     # +0.47R, n=37
    assert swing_class_ok("bull_flag", "daily")        # +0.19R, n=25

    # The original neckline families stay, dailies included (the
    # entry-location experiment — retest-limit entries vs the
    # breakout-close entries their negative priors were graded on).
    assert swing_class_ok("higher_low", "weekly")
    assert swing_class_ok("higher_low", "daily")
    assert swing_class_ok("double_bottom", "daily")
    assert swing_class_ok("inverse_hs", "weekly")

    # The exclusion that needs the JOINT gate: ema_bounce daily passes the
    # pattern filter AND the timeframe filter individually. -0.37R over
    # 162 episodes does not get half-sized — it gets excluded (the shorts
    # lesson).
    assert "ema_bounce" in SWING_PATTERNS
    assert not swing_class_ok("ema_bounce", "daily")

    # Negative-prior and never-admitted classes stay out.
    assert not swing_class_ok("range_breakout", "weekly")   # -0.08R, n=125
    assert not swing_class_ok("range_breakout", "daily")    # -0.12R, n=87
    assert not swing_class_ok("falling_wedge", "weekly")    # -0.15R, n=30
    assert not swing_class_ok("higher_low", "4h")           # never the thesis

    # Every class is (pattern, timeframe) over the two swing timeframes —
    # a malformed entry here would silently match nothing in the query.
    for pat, tf in SWING_CLASSES:
        assert tf in ("weekly", "daily"), (pat, tf)
        assert pat == pat.lower() and " " not in pat, (pat, tf)

    print("ok — positive-prior classes admitted, ema_bounce daily blocked "
          "by the joint gate, negative-prior classes stay retired")


if __name__ == "__main__":
    main()
