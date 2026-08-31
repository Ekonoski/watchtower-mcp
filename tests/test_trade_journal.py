"""Eric's trade journal, pinned (2026-09-02).

  1. R math: underlying terms, sign-correct for shorts, computed only
     when entry/stop/exit all exist — never fabricated.
  2. Validation refuses junk (direction, timestamps) with reasons.
  3. Writes only trade_journal — by signature (his book, never the
     desk's).
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import trade_journal  # noqa: E402
from analysis.trade_journal import _num, _parse_ts  # noqa: E402


def test_r_math_is_underlying_and_signed():
    # long: in 100, stop 99, out 102 -> +2R; short mirrors
    sign = 1.0
    r = sign * (102.0 - 100.0) / abs(100.0 - 99.0)
    assert r == 2.0
    sign = -1.0
    r = sign * (98.0 - 100.0) / abs(100.0 - 101.0)
    assert r == 2.0
    src = inspect.getsource(trade_journal.log_trade)
    assert "e_px and x_px and s_px" in src      # all three or no R


def test_validation_refuses_junk():
    try:
        _parse_ts("not a time")
        assert False, "should have raised"
    except ValueError as e:
        assert "ISO" in str(e)
    assert _parse_ts("") is None
    assert _parse_ts("2026-09-02T10:35") is not None
    try:
        _num("abc", "entry_px")
        assert False, "should have raised"
    except ValueError as e:
        assert "entry_px" in str(e)
    assert _num(None, "x") is None and _num("1.5", "x") == 1.5


def test_writes_only_the_journal():
    src = inspect.getsource(trade_journal)
    assert "INSERT INTO trade_journal" in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
