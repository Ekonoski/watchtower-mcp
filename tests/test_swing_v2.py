"""Swing v2 (2026-09-10), pinned:
  1. The confirm fill: a touch alone fills nothing; the first completed
     bar CLOSING back through the trigger after the touch fills at that
     close (the touch bar counts if its own close is back through);
     closes above the trigger with no touch never fill; a lost level
     still goes DOA / reclaim exactly as v1.
  2. v1 semantics are untouched when confirm is off (the default).
  3. Book birth touches every place at once: the schema allowlists
     (migration 069), the writer's book, the loop's fill / halt / exit
     branches and the settle (SWING_BOOKS, no bare 'swing' literal
     left in the decision paths), the audit's exit vocabulary, and the
     readers that count swing specs.
  4. The retired classes are named with reasons (test_class_admission
     pins the refusal).
"""
import datetime as dt
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import paper_trader as pt  # noqa: E402
from analysis.paper_trader import (  # noqa: E402
    SWING_BOOK, SWING_BOOKS, _swing_fill, swing_class_ok)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _b(hh, mm, o, c, h, l):
    return (dt.datetime(2026, 9, 14, hh, mm), o, c, h, l)


def test_confirm_fill_needs_a_touch_then_a_close_back_through():
    trig, stop = 100.0, 95.0
    # closes above the trigger, never touched → nothing (price sitting above a level is not proof)
    bars = [_b(9, 30, 101, 102, 103, 100.5), _b(9, 45, 102, 101.5, 102.5, 101)]
    assert _swing_fill("long", trig, stop, bars, confirm=True) == (None, None, None)
    # v1 would not fill here either (no touch)
    assert _swing_fill("long", trig, stop, bars) == (None, None, None)
    # a touch bar that closes back above fills at ITS close, kind 'confirm'
    bars2 = bars + [_b(10, 0, 101, 100.8, 101.2, 99.6)]
    assert _swing_fill("long", trig, stop, bars2, confirm=True) == ("fill", 100.8, "confirm")
    # v1 fills the same tape at the trigger on the touch
    assert _swing_fill("long", trig, stop, bars2) == ("fill", 100.0, "touch")
    # a touch bar closing BELOW the trigger arms but does not fill; the
    # next bar opens below (level lost) and closes back over → reclaim, as v1
    bars3 = bars + [_b(10, 0, 101, 99.7, 101.2, 99.2), _b(10, 15, 99.5, 100.4, 100.6, 99.3)]
    assert _swing_fill("long", trig, stop, bars3, confirm=True) == ("fill", 100.4, "reclaim")
    # a touch bar closing below, then a bar opening ABOVE and closing above → confirm at that close
    bars4 = bars + [_b(10, 0, 101, 99.7, 101.2, 99.2), _b(10, 15, 100.2, 100.9, 101.0, 100.1)]
    assert _swing_fill("long", trig, stop, bars4, confirm=True) == ("fill", 100.9, "confirm")
    # dead on arrival is unchanged
    doa = [_b(9, 30, 94.0, 96.0, 96.5, 93.5)]
    assert _swing_fill("long", trig, stop, doa, confirm=True) == ("doa", None, None)


def test_book_birth_touches_every_place():
    assert SWING_BOOK == "swing_v2" and SWING_BOOKS == ("swing", "swing_v2")
    src = inspect.getsource(pt)
    # no decision path compares the book to a bare 'swing' literal any more
    assert 'book == "swing"' not in src and "book='swing'" not in src
    assert src.count("s.book = ANY(%s)") >= 2          # writer's open-position guard + settle
    assert 'specs.append((today, SWING_BOOK,' in src
    assert 'confirm=(book == SWING_BOOK)' in src
    assert 'kind in ("reclaim", "confirm")' in src     # geometry re-check at the confirm premium too
    mig = open(os.path.join(ROOT, "migrations", "069_swing_v2.sql")).read()
    assert "'swing_v2'::text" in mig and "'confirm'::text" in mig
    from analysis.ledger_audit import LEGAL_EXITS
    assert LEGAL_EXITS["swing_v2"] == LEGAL_EXITS["swing"]
    for path in ("alerts/spec_ping.py", "analysis/options_expression.py"):
        text = open(os.path.join(ROOT, path)).read()
        assert "'swing_v2'" in text, path


def test_retired_classes_are_refused_and_the_rest_admit():
    assert not swing_class_ok("higher_low", "daily")
    assert not swing_class_ok("double_bottom", "daily")
    assert swing_class_ok("higher_low", "weekly") and swing_class_ok("inverse_hs", "daily")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
