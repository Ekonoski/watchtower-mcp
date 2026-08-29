"""The red-dot study's pure core, pinned (2026-08-29).

  1. The mirror is EXACT: find_red_dots is find_dots on the negated
     series — a cross-down above zero on the original is a cross-up
     below zero on the mirror, so the graded detector is reused and
     can never drift.
  2. A cross-down BELOW zero is not a red dot here (the zero-line leg,
     mirrored).
  3. Run-up buckets cut where the spec says.
  4. Writes only its own tables — by signature.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.reddot_study import find_red_dots, ru_bucket  # noqa: E402


def test_cross_down_above_zero_is_a_red_dot():
    #        i:   0     1     2
    wt1 = [40.0, 30.0, 20.0]
    wt2 = [35.0, 33.0, 25.0]
    # i=1: wt1 drops below wt2 with wt2=+33 (above zero) → RED DOT.
    assert find_red_dots(wt1, wt2) == [1]


def test_cross_down_below_zero_is_not_a_red_dot():
    wt1 = [-10.0, -20.0]
    wt2 = [-15.0, -12.0]
    # Cross-down at i=1 but wt2=-12 (below zero) → not a red dot.
    assert find_red_dots(wt1, wt2) == []


def test_mirror_equals_greendot_on_negated_series():
    from analysis.greendot_study import find_dots
    wt1 = [40.0, 30.0, 20.0, 25.0, None, 50.0, 35.0]
    wt2 = [35.0, 33.0, 25.0, 22.0, 10.0, 40.0, 41.0]
    neg1 = [None if v is None else -v for v in wt1]
    neg2 = [None if v is None else -v for v in wt2]
    assert find_red_dots(wt1, wt2) == find_dots(neg1, neg2)


def test_runup_buckets():
    assert ru_bucket(2.50) == "gte200"
    assert ru_bucket(1.50) == "b100_200"
    assert ru_bucket(0.75) == "b50_100"
    assert ru_bucket(0.20) == "lt50"


def test_writes_only_its_own_tables():
    from analysis import reddot_study
    src = inspect.getsource(reddot_study)
    assert "INSERT INTO reddot_dots" in src
    assert "INSERT INTO reddot_progress" in src
    assert "INSERT INTO greendot" not in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
