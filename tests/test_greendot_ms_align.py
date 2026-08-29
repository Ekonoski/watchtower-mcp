"""The daily-dot alignment pass, pinned (2026-08-29).

  1. CLEAR_WINDOW is 15 trading days — a daily trade's patience, not
     the 6-month window of the 16D entry study.
  2. The trigger machinery is REUSED from greendot_ema_entry (ema,
     find_above_both imported, never reimplemented) — by signature.
  3. Writes only greendot_ms_align — by signature; the multiscale
     base table is never touched.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_ms_align import CLEAR_WINDOW  # noqa: E402


def test_clear_window_is_a_daily_trades_patience():
    assert CLEAR_WINDOW == 15


def test_reuses_ema_machinery_by_signature():
    from analysis import greendot_ms_align
    src = inspect.getsource(greendot_ms_align)
    assert "from analysis.greendot_ema_entry import ema, find_above_both" in src
    assert "def ema(" not in src and "def find_above_both(" not in src


def test_weekly_uses_native_scale_patience():
    from analysis.greendot_ms_align import CLEAR_WINDOW_W
    assert CLEAR_WINDOW_W == 15   # 15 WEEKLY bars — symmetric with daily


def test_weekly_reuses_weekly_bar_machinery():
    from analysis import greendot_ms_align
    src = inspect.getsource(greendot_ms_align)
    assert "week_end_indices" in src
    assert "def week_end_indices" not in src   # imported, not rebuilt


def test_writes_only_its_own_table():
    from analysis import greendot_ms_align
    src = inspect.getsource(greendot_ms_align)
    assert "INSERT INTO greendot_ms_align" in src
    assert "INSERT INTO greendot_dots_ms" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src
    assert "INSERT INTO paper_" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
