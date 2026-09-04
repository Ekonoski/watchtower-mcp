"""The journal's legs vocabulary (2026-09-04): fixed list, unknown tags
refused by name, every tag grouped and marked checkable/eye-only, the
per-leg grade states both sides, and both writers carry the column.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import journal_legs as jl  # noqa: E402
from analysis import trade_journal  # noqa: E402


def test_vocabulary_shape():
    assert len(jl.VOCAB) >= 30
    for tag, (group, checkable, meaning) in jl.VOCAB.items():
        assert tag == tag.lower() and " " not in tag
        assert group in jl.GROUPS and isinstance(checkable, bool) and meaning
    # the five legs Eric named on 9/4 are all present
    for t in ("confirmed", "anticipated", "partial_at_level", "runner_next_level",
              "chop_expected", "on_flip", "pre_holiday", "no_partial"):
        assert t in jl.VOCAB


def test_normalize_refuses_unknown():
    assert jl.normalize("") is None and jl.normalize([]) is None
    assert jl.normalize("Confirmed, on_flip confirmed") == ["confirmed", "on_flip"]
    try:
        jl.normalize("confirmed vibes")
        assert False, "unknown tag must be refused"
    except ValueError as e:
        assert "vibes" in str(e) and "fixed" in str(e)


def test_leg_grade_states_both_sides():
    rows = [(["confirmed", "partial_at_level"], 1.0), (["confirmed"], 0.5),
            (["anticipated"], -0.8), (["anticipated", "no_partial"], -0.6),
            (["confirmed"], None)]                       # open row ignored
    g = {d["tag"]: d for d in jl.leg_grade(rows)}
    assert g["confirmed"]["n_with"] == 2 and g["confirmed"]["wins_with"] == 2
    assert g["confirmed"]["n_without"] == 2 and abs(g["confirmed"]["avg_without"] + 0.7) < 1e-9
    assert g["anticipated"]["spread"] < 0 < g["confirmed"]["spread"]
    assert jl.leg_grade(rows)[0]["tag"] in ("anticipated", "no_partial")   # worst spread first
    lines = jl.render_grade(jl.leg_grade(rows), 4)
    assert any("below ~30" in ln for ln in lines) and any("eye-only" in ln for ln in lines)
    assert jl.render_grade([], 0)[-1].startswith("By leg")


def test_writers_carry_legs():
    for fn in (trade_journal.log_trade, trade_journal.log_skip):
        src = inspect.getsource(fn)
        assert "legs" in src and "normalize as _legs" in src
    summ = inspect.getsource(trade_journal.journal_summary)
    assert "leg_grade" in summ and "render_grade" in summ


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
