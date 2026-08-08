"""The confirmation shadow, pinned with 2026-08-07's real numbers.

Decision (Eric, 2026-08-08): the swing book keeps resting-limit fills at
the trigger — but whether entries SHOULD demand a completed 15m close back
through (the way reclaims and the gamma book do) is settled by measurement.
Every touch fill records what a confirmation-gated desk would have done
with the same spec: its entry (the first 15m close back through after the
touch) or the trade it never took. The wick rule is untouched — the shadow
itself confirms on completed closes only.

The trap this test pins: for a long, every bar BEFORE the dip closes above
the trigger. Counting one of those as confirmation would make the shadow
equal the actual on every trade and the whole measurement a tautology.

Standalone per house convention:  python3 tests/test_confirm_shadow.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import _confirm_shadow  # noqa: E402

T = dt.datetime(2026, 8, 7, 9, 45, tzinfo=dt.timezone.utc)


def bar(op, cl, hi, lo, mins=0):
    return (T + dt.timedelta(minutes=mins), op, cl, hi, lo)


def main():
    # CAE, Friday (real bars): price above the 26.65 trigger, dips to touch
    # it on the second bar, and that same bar closes 27.00 — back through.
    # The limit desk owns it at 26.65; the confirmation desk pays 27.00.
    # The 0.35 premium is the cost of confirmation, priced not argued.
    # The FIRST bar closes 26.80 above the trigger but pre-touch — it must
    # NOT count as confirmation (that's price sitting above the level).
    out = _confirm_shadow("long", 26.65,
                          [bar(26.90, 26.80, 26.95, 26.70),
                           bar(26.78, 27.00, 27.05, 26.65, 15)])
    assert out is not None and out[0] == 27.00, out
    assert out[1] == T + dt.timedelta(minutes=15), out

    # Touch bar closes BELOW the trigger, next bar closes back through:
    # confirmation arrives on the later bar, at ITS close.
    out = _confirm_shadow("long", 26.65,
                          [bar(26.90, 26.80, 26.95, 26.70),
                           bar(26.78, 26.55, 26.80, 26.60, 15),
                           bar(26.55, 26.90, 26.95, 26.50, 30)])
    assert out is not None and out[0] == 26.90, out

    # Touch, then every close stays below the trigger into the close: the
    # confirmation desk never enters. (The record: confirm_status
    # 'no_confirm' — the limit desk owns a trade the confirmed desk skipped.
    # If that trade stops out, confirmation just saved 1R; the ledger counts
    # both sides.)
    out = _confirm_shadow("long", 26.65,
                          [bar(26.90, 26.80, 26.95, 26.70),
                           bar(26.78, 26.55, 26.80, 26.60, 15),
                           bar(26.55, 26.40, 26.60, 26.30, 30)])
    assert out is None, out

    # No touch at all: nothing to confirm.
    out = _confirm_shadow("long", 26.65,
                          [bar(26.90, 26.80, 26.95, 26.70)])
    assert out is None, out

    # Short mirror: trigger 50 from below. Pre-touch bar closes 49.6 (below
    # trig — must not count). Touch bar spikes to 50.1, closes 49.8 — back
    # through for a short. Shadow entry 49.8.
    out = _confirm_shadow("short", 50.0,
                          [bar(49.4, 49.6, 49.8, 49.3),
                           bar(49.7, 49.8, 50.1, 49.5, 15)])
    assert out is not None and out[0] == 49.8, out

    print("ok — shadow confirms on the first 15m close back through AFTER "
          "the touch, pre-touch closes never count, no-confirm means the "
          "confirmation desk skipped the trade")


if __name__ == "__main__":
    main()
