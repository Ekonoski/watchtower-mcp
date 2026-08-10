"""The spec-writer's scan-freshness gate, pinned with the row that armed.

The bug this pins (reached the live book 2026-08-10, day one): the 6:45
pattern scan died in a database brownout AFTER claiming its daily slot, so
pattern_scan still held Friday's rows when the 7:40 spec-writer read it —
and the writer had no freshness gate, so the ENTIRE swing book armed off
the stale menu. The visible tip: TNDM's trigger (18.16, from Friday's
scan) sat 23% below the 22.38 market it woke up to, far outside the 0-4%
entry band a fresh scan enforces. The fill model kept it phantom-proof
(a limit 23% below market can't fill on prices that never print), but the
book spent slots on Friday's leftovers.

The gate: a candidate row is armable only if it was scanned TODAY (ET).
A dead-scan morning shrinks the book to zero, loudly — it never pads it.

Standalone per house convention:  python3 tests/test_spec_freshness_gate.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import fresh_swing_rows  # noqa: E402

TODAY = dt.date(2026, 8, 10)          # Monday — the morning it bit
FRIDAY = dt.date(2026, 8, 7)          # the stale scan date it bit with

# Real rows from the 2026-08-10 book: TNDM is the Friday-stale spec that
# armed; WIX stands in for a same-day row a healthy 6:45 scan would stamp.
TNDM_STALE = ("TNDM", "daily", "higher_low", "bullish", 18.16, 22.37, 16.14, 78, FRIDAY)
MOS_STALE = ("MOS", "daily", "higher_low", "bullish", 23.3951, 26.37, 21.44, 74, FRIDAY)
WIX_FRESH = ("WIX", "daily", "higher_low", "bullish", 59.50, 73.38, 51.62, 81, TODAY)


def main():
    # Mixed menu: only the today-stamped row survives; the stale ones are
    # counted and their latest date surfaced for the loud log line.
    fresh, n_stale, stale_latest = fresh_swing_rows(
        [TNDM_STALE, WIX_FRESH, MOS_STALE], TODAY)
    assert fresh == [WIX_FRESH[:-1]], f"fresh set wrong: {fresh}"
    assert n_stale == 2 and stale_latest == FRIDAY

    # The 2026-08-10 morning exactly: every row stale -> the book shrinks
    # to zero. It must NOT fall back to the stale rows.
    fresh, n_stale, stale_latest = fresh_swing_rows([TNDM_STALE, MOS_STALE], TODAY)
    assert fresh == [] and n_stale == 2 and stale_latest == FRIDAY

    # Healthy morning: everything fresh, nothing dropped, no stale stamp.
    fresh, n_stale, stale_latest = fresh_swing_rows([WIX_FRESH], TODAY)
    assert fresh == [WIX_FRESH[:-1]] and n_stale == 0 and stale_latest is None

    # Empty scan table: empty book, zero stale — not an error.
    assert fresh_swing_rows([], TODAY) == ([], 0, None)

    print("ok — stale rows shrink the book and are counted; they never arm")


if __name__ == "__main__":
    main()
