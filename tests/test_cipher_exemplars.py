"""The cipher exemplar logger, pinned (2026-08-27).

The contract:
  1. normalize() rejects garbage loudly (the message reaches the human)
     and accepts the human spellings (1d→daily, w→weekly).
  2. Staleness is per-timeframe (1h ages in a day; weekly gets 8) and a
     stale state is a NAMED hole, never silently captured as fresh.
  3. The module writes only cipher_exemplars — it cannot touch the
     books, the scan tables, or the screens. By signature.
  4. Passes are first-class: the label set is exactly {take, pass} —
     the definition lives in the boundary, so refusals must be
     recordable with the same machinery as entries.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.cipher_exemplars import (LABELS, normalize,  # noqa: E402
                                       staleness)


def test_normalize_accepts_human_spellings():
    assert normalize("nflx", "1d", "TAKE") == ("NFLX", "daily", "take")
    assert normalize(" chwy ", "W", "pass") == ("CHWY", "weekly", "pass")
    assert normalize("qqq", "4H", "take") == ("QQQ", "4h", "take")


def test_normalize_rejects_loudly():
    for bad in [("", "1h", "take"), ("NFLX", "3d", "take"),
                ("NFLX", "1h", "maybe"), ("NOT A TICKER", "1h", "take")]:
        try:
            normalize(*bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError as e:
            assert str(e)   # the message is the interface


def test_staleness_is_per_timeframe_and_named():
    now = dt.datetime(2026, 8, 27, 20, 0, tzinfo=dt.timezone.utc)
    fresh = now - dt.timedelta(hours=3)
    old = now - dt.timedelta(days=3)
    assert staleness("1h", fresh, now) is None
    assert "3d old" in staleness("1h", old, now)
    assert staleness("weekly", old, now) is None      # weekly gets 8 days
    assert staleness("4h", None, now)                 # missing stamp = hole


def test_passes_are_first_class():
    assert LABELS == {"take", "pass"}


def test_writes_only_its_own_table_by_signature():
    from analysis import cipher_exemplars
    src = inspect.getsource(cipher_exemplars)
    assert "INSERT INTO cipher_exemplars" in src
    for forbidden in ("INSERT INTO paper_", "UPDATE paper_",
                      "INSERT INTO oscillator", "UPDATE oscillator",
                      "DELETE FROM"):
        assert forbidden not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
