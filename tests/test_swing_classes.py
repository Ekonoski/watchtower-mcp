"""The swing book's class gate, pinned.

2026-08-08: the book expanded from the three neckline families to every
(pattern, timeframe) class with a positive backtest prior. The trap this
test exists for: the SQL query filters pattern and timeframe INDEPENDENTLY,
so ema_bounce daily — the worst class on the board (v6: -0.16R, n=46,979)
— passes both individual filters and only the joint class gate keeps it
out. A future "simplification" back to flat lists would quietly re-admit
it and fail no test but this one.

Priors quoted are pattern_backtest v6 (read 2026-08-10 — the first replay
after the path-1 censorship fix; v4's numbers are struck and must not be
cited). Updated same day for the v6 weekly additions: every weekly class
beats its daily twin, and four weekly-only classes joined the allowlist.

Standalone per house convention:  python3 tests/test_swing_classes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import (  # noqa: E402
    SWING_CLASSES, SWING_PATTERNS, swing_class_ok)


def main():
    # The 2026-08-08 additions, re-graded by v6:
    assert swing_class_ok("asc_triangle", "weekly")    # +0.28R, n=1,896
    assert swing_class_ok("ema_bounce", "weekly")      # +0.84R, n=8,188 — best on the board
    assert swing_class_ok("bull_flag", "weekly")       # +0.24R, n=2,561
    assert swing_class_ok("asc_triangle", "daily")     # +0.08R, n=6,631
    assert swing_class_ok("bull_flag", "daily")        # +0.09R, n=11,496

    # The original neckline families stay, dailies included (the
    # entry-location experiment — retest-limit entries vs the
    # breakout-close entries their priors were graded on).
    assert swing_class_ok("higher_low", "weekly")
    assert swing_class_ok("higher_low", "daily")
    assert swing_class_ok("double_bottom", "daily")
    assert swing_class_ok("inverse_hs", "weekly")

    # The 2026-08-10 weekly-only additions (each positive at scale in v6;
    # wma_touch rides the goat study's own provisional prior):
    assert swing_class_ok("cup_handle", "weekly")      # +0.24R, n=2,727
    assert swing_class_ok("range_breakout", "weekly")  # +0.19R, n=3,423
    assert swing_class_ok("falling_wedge", "weekly")   # +0.12R, n=1,750
    assert swing_class_ok("wma_touch", "weekly")       # goat: 82%/+5%, n=2,653

    # The exclusion that needs the JOINT gate: ema_bounce daily passes the
    # pattern filter AND the timeframe filter individually. -0.16R over
    # 46,979 episodes does not get half-sized — it gets excluded (the
    # shorts lesson).
    assert "ema_bounce" in SWING_PATTERNS
    assert not swing_class_ok("ema_bounce", "daily")

    # Daily twins of the weekly-only classes stay out — +0.01 to +0.09
    # edges are too thin to spend capped book slots on.
    assert not swing_class_ok("cup_handle", "daily")     # +0.01R, n=11,795
    assert not swing_class_ok("range_breakout", "daily")  # +0.08R, n=22,492
    assert not swing_class_ok("falling_wedge", "daily")   # +0.09R, n=7,059
    assert not swing_class_ok("wma_touch", "daily")       # weekly is the thesis
    assert not swing_class_ok("higher_low", "4h")         # never the thesis

    # Every class is (pattern, timeframe) over the two swing timeframes —
    # a malformed entry here would silently match nothing in the query.
    for pat, tf in SWING_CLASSES:
        assert tf in ("weekly", "daily"), (pat, tf)
        assert pat == pat.lower() and " " not in pat, (pat, tf)

    print("ok — positive-prior classes admitted (15), thin/negative daily "
          "twins blocked by the joint gate, 4h never admitted")


if __name__ == "__main__":
    main()
