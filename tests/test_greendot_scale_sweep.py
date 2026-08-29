"""The block-size sweep, pinned (2026-08-29).

  1. The sweep fills the curve around the scales already graded —
     none of them duplicated.
  2. Machinery reused by signature: blocks_nd (fixed anchor,
     no-repaint proven at its own tests), find_dots, bucket.
  3. Writes only the multiscale tables — by signature; scale names
     are namespaced 'nd<k>' so they can never collide with
     daily/weekly rows.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_scale_sweep import SWEEP_BLOCKS  # noqa: E402


def test_sweep_fills_the_curve_without_duplicates():
    assert SWEEP_BLOCKS == (3, 8, 12, 21, 26, 32)
    for have in (1, 5, 15, 16):        # daily, ~weekly, robust15, 16D
        assert have not in SWEEP_BLOCKS


def test_reuses_proven_machinery_by_signature():
    from analysis import greendot_scale_sweep
    src = inspect.getsource(greendot_scale_sweep)
    assert "from analysis.greendot_robust15 import blocks_nd" in src
    assert "from analysis.greendot_study import bucket, find_dots" in src
    assert "def find_dots" not in src and "def blocks_nd" not in src


def test_scale_names_are_namespaced():
    from analysis import greendot_scale_sweep
    src = inspect.getsource(greendot_scale_sweep)
    assert 'f"nd{block}"' in src


def test_writes_only_multiscale_tables():
    from analysis import greendot_scale_sweep
    src = inspect.getsource(greendot_scale_sweep)
    assert "INSERT INTO greendot_dots_ms" in src
    assert "INSERT INTO greendot_ms_progress" in src
    assert "INSERT INTO greendot_dots " not in src
    assert "INSERT INTO greendot_dots\n" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src
    assert "INSERT INTO paper_" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
