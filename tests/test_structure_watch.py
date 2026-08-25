"""The intraday structure watcher, pinned (2026-08-24).

  1. Touch detection: first RTH bar whose low reaches the shelf.
  2. One ping per (day, ticker, level): the claim ref quantizes to
     cents — a shelf is a price, not a feeling.
  3. The alert message carries the discipline: watch prompt / not an
     entry / forward-return graded / one ping per level per day.
  4. Watch-only by signature: no writes to the paper tables, and the
     detector is the SAME find_defense the shadow and study use — one
     definition, four readers now.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts.structure_watch import (first_touch_idx, format_watch_alert,  # noqa: E402
                                    watch_ref)

T0 = dt.datetime(2026, 8, 25, 9, 30)


def _bar(i, low):
    return (T0 + dt.timedelta(minutes=15 * i), 100.0, 100.5, 101.0, low, 1000.0)


def test_first_touch_idx():
    bars = [_bar(0, 84.6), _bar(1, 84.1), _bar(2, 83.5), _bar(3, 83.9)]
    assert first_touch_idx(bars, 83.69) == 2
    assert first_touch_idx(bars, 83.0) is None
    assert first_touch_idx([], 83.69) is None


def test_ref_quantizes():
    d = dt.date(2026, 8, 25)
    assert watch_ref(d, "TRU", 83.69) == "2026-08-25:TRU:83.69"
    assert watch_ref(d, "TRU", 83.690001) == watch_ref(d, "TRU", 83.69)
    assert watch_ref(d, "TRU", 83.70) != watch_ref(d, "TRU", 83.69)


def test_alert_message_carries_the_discipline():
    res = {"px": 84.1, "at": T0, "premium_pct": 0.0049,
           "defense_vol": 1200000.0, "base_vol": 800000.0}
    m = format_watch_alert("TRU", 83.69, 13, res, "10:45")
    assert "DEFENDED retest" in m and "13-touch" in m
    assert "watch prompt, not an entry" in m
    assert "forward returns" in m and "one ping per level per day" in m


def test_watch_only_by_signature():
    from alerts import structure_watch
    src = inspect.getsource(structure_watch)
    for forbidden in ("INSERT INTO paper", "UPDATE paper"):
        assert forbidden not in src, forbidden
    assert "find_defense" in src            # one detector, four readers
    assert "claim_and_send" in src          # at-most-once pings
    assert "ingestion_log" in inspect.getsource(
        __import__("alerts.scheduler", fromlist=["scheduler"]))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
