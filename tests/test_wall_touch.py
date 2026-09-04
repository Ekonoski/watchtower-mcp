"""The wall-touch prior (2026-09-02), pinned:
  1. touch_outcome: first bar AFTER the board whose range contains the
     level is the touch; bars at/before the board never count; no bars
     after the board = a hole (None), never False.
  2. touched_1h is relative to the board time.
  3. bucket() edges; prior() reads graded rows only (holes reported).
  4. The module writes only wall_touch_events (by signature).
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import wall_touch_study as w  # noqa: E402


def _bars(t0, specs):
    return [(t0 + dt.timedelta(minutes=15 * i), o, h, l, c)
            for i, (o, h, l, c) in enumerate(specs)]


def test_touch_outcome_semantics():
    utc = dt.timezone.utc
    board = dt.datetime(2026, 9, 2, 13, 35, tzinfo=utc)      # 9:35 ET
    t0 = dt.datetime(2026, 9, 2, 13, 30, tzinfo=utc)         # 9:30 bar
    bars = _bars(t0, [(760, 766, 759, 765),   # 9:30 bar — at/before board: ignored
                      (765, 767, 764, 766),   # 9:45
                      (766, 768, 765, 767),   # 10:00
                      (767, 771, 766, 770)])  # 10:15 touches 770
    touched, ts, t1h = w.touch_outcome(bars, 770.0, board)
    assert touched is True and ts == bars[3][0] and t1h is True
    # level only inside the pre-board bar -> not a touch
    touched, ts, t1h = w.touch_outcome(bars, 759.5, board)
    assert touched is False and ts is None and t1h is False
    # no bars after the board -> hole
    assert w.touch_outcome(bars[:1], 765.0, board) == (None, None, None)
    # touched after 1h -> touched_1h False
    late = _bars(t0, [(760, 761, 759, 760)] * 6 + [(760, 771, 759, 770)])
    touched, ts, t1h = w.touch_outcome(late, 770.0, board)
    assert touched is True and t1h is False


def test_bucket_edges():
    assert w.bucket(0.1) == (0.0, 0.25)
    assert w.bucket(-0.25) == (0.25, 0.5)
    assert w.bucket(2.99) == (2.0, 3.0)
    assert w.bucket(3.5) is None


def test_prior_reads_graded_rows_only():
    class Cur:
        def execute(self, sql, params):
            assert "touched IS NOT NULL" in sql
            self.params = params
        def fetchone(self):
            return (20, 13, 4)          # n graded, hits, holes
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class Conn:
        def cursor(self): return Cur()
    assert w.prior(Conn(), "put_wall", "slippery", 0.6) == (65.0, 20, 4)


def test_writes_own_table_only():
    src = inspect.getsource(w)
    assert "INSERT INTO wall_touch_events" in src
    for forbidden in ("paper_trades", "paper_specs", "gex_levels ",
                      "INSERT INTO gex", "UPDATE gex"):
        assert forbidden not in src


def test_scheduler_daily_pass_imports_its_clock():
    # 2026-09-03 16:50: the cron body used `dt` without importing it and
    # died with NameError on its first scheduled fire; the boot backfill
    # masked it. The job must carry its own import.
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "alerts", "scheduler.py")).read()
    i = src.index("def _wall_touch():")
    body = src[i:i + 500]
    assert "import datetime as _dt" in body and "_dt.datetime.now(et).date()" in body


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
