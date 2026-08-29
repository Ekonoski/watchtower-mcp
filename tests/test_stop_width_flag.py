"""The stop-width flag, pinned (2026-08-29 — the external-audit catch,
verified against the book: MLAB 50.2% risk, DSGR 1.1%).

  1. The live outliers both flag; a normal-width stop doesn't.
  2. It's a WARNING, never a gate — by signature, the writer only
     appends it to the rationale.
  3. Holes return None, never a guess.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import (STOP_WIDTH_TIGHT,  # noqa: E402
                                   STOP_WIDTH_WIDE, stop_width_flag)


def test_the_mlab_width_flags_wide():
    flag = stop_width_flag(111.29, 55.45)      # the live 50.2% row
    assert flag is not None and "wide" in flag and "50.2" in flag


def test_the_dsgr_width_flags_tight():
    flag = stop_width_flag(34.83, 34.45)       # the live 1.1% row
    assert flag is not None and "noise-tight" in flag


def test_normal_width_is_silent():
    assert stop_width_flag(100.0, 92.0) is None    # 8% — a normal stop
    assert stop_width_flag(100.0, 75.0) is None    # 25% — at the line, ok


def test_holes_return_none():
    assert stop_width_flag(None, 92.0) is None
    assert stop_width_flag(100.0, None) is None
    assert stop_width_flag(0, 0) is None


def test_thresholds_are_the_declared_ones():
    assert STOP_WIDTH_WIDE == 25.0 and STOP_WIDTH_TIGHT == 2.0


def test_warning_never_gates_by_signature():
    from analysis import paper_trader
    src = inspect.getsource(paper_trader)
    # The flag's only uses: rationale append + log. It never appears
    # in a filter, continue, or kept-list mutation.
    seg = src.split("swf = stop_width_flag")[1][:400]
    assert "rationale +=" in seg and "log.warning" in seg
    assert "continue" not in seg and "kept.remove" not in seg


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
