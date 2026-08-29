"""The dot book, pinned (2026-08-29).

  1. An unlogged entry price is a HOLE in the render and an UNKNOWN
     in realized returns — never a guess (fill honesty for the live
     book, same spirit as the paper desk's).
  2. Losers lead in the closed section — by the ORDER BY, worst
     realized ratio first.
  3. The ledger writes only its own table; the dot anchor is READ
     from greendot_dots, never written.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import dot_book  # noqa: E402


def test_unlogged_fill_is_a_hole_never_a_guess():
    src = inspect.getsource(dot_book)
    assert "UNLOGGED" in src            # open-position hole
    assert "UNKNOWN" in src             # realized-return hole
    assert "entry px was never logged" in src


def test_losers_lead_in_closed_section():
    src = inspect.getsource(dot_book.render_book)
    assert "exit_px/entry_px END ASC" in src
    assert "worst first" in src


def test_writes_only_its_own_table():
    src = inspect.getsource(dot_book)
    assert "INSERT INTO dot_book" in src
    assert "UPDATE dot_book" in src     # exits close rows in place
    assert "INSERT INTO greendot" not in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE greendot" not in src and "UPDATE paper_" not in src
    assert "DELETE FROM" not in src


def test_prior_rides_the_render():
    assert "survivors-only" in dot_book.PRIOR_LINE
    assert "excursion" in dot_book.PRIOR_LINE


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
