"""The day-bias audition book's decision core, pinned (2026-08-23).

The book trades the STUDY's definition — nothing else:
  1. 9:30 open at/below PDH -> no_bias (the stand-aside IS the trade).
  2. A PDH touch before 10:30 CANCELS the day — that bucket graded as
     a coin flip with MAE>MFE; buying it would be trading the chop on
     purpose.
  3. The first >=10:30 touch fills at PDH (resting limit's execution
     fact), disaster stop 0.75% below.
  4. The wick rule holds: a wick through the disaster stop is not a
     stop; only a 15m CLOSE through is.
  5. No touch all day -> stays 'waiting' intraday (settle declares
     no_retest at EOD, not the loop mid-day).
  6. Measurement only, by signature: the module never touches the
     swing or gamma books' specs.
"""
import datetime as dt
import inspect
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.day_bias import BOOK, DISASTER_STOP_PCT, decide  # noqa: E402

ET = ZoneInfo("America/New_York")
PDH = 500.0


def _bar(h, m, o, c, hi, lo, v=1000.0):
    return (dt.datetime(2026, 8, 24, h, m, tzinfo=ET), o, c, hi, lo, v)


def test_no_bias_when_open_at_or_below_pdh():
    bars = [_bar(9, 30, 499.5, 501.0, 501.5, 499.0)]
    assert decide(bars, PDH)["state"] == "no_bias"


def test_early_touch_cancels_the_day():
    bars = [_bar(9, 30, 501.0, 500.8, 501.5, 500.4),
            _bar(9, 45, 500.8, 500.6, 501.0, 499.9)]   # touch at 9:45
    res = decide(bars, PDH)
    assert res["state"] == "cancelled_early"
    assert res["at"].time() == dt.time(9, 45)


def test_late_touch_fills_at_pdh():
    bars = [_bar(9, 30, 501.0, 501.5, 501.8, 500.6),
            _bar(9, 45, 501.5, 501.2, 501.7, 500.7),
            _bar(10, 30, 501.2, 500.4, 501.3, 499.8)]  # first touch 10:30
    res = decide(bars, PDH)
    assert res["state"] == "filled"
    assert res["entry"] == PDH
    assert res["at"].time() == dt.time(10, 30)
    assert abs(res["stop"] - PDH * (1 - DISASTER_STOP_PCT)) < 1e-9


def test_wick_through_stop_excused_close_through_stops():
    stop = PDH * (1 - DISASTER_STOP_PCT)          # 496.25
    bars = [_bar(9, 30, 501.0, 501.5, 501.8, 500.6),
            _bar(10, 30, 501.2, 500.4, 501.3, 499.8),   # fill
            _bar(10, 45, 500.4, 497.0, 500.5, 495.0),   # WICK to 495, close 497
            _bar(11, 0, 497.0, 496.0, 497.2, 495.8)]    # CLOSE 496 < stop
    res = decide(bars, PDH)
    assert res["state"] == "stopped"
    assert res["stop_at"].time() == dt.time(11, 0)
    assert res["stop_px"] == 496.0
    # Without the closing bar, the wick alone must NOT stop it.
    res2 = decide(bars[:3], PDH)
    assert res2["state"] == "filled", "a wick through the stop is not a stop"
    assert stop > 495.0  # the wick did pierce it — that's the point


def test_no_touch_stays_waiting_intraday():
    bars = [_bar(9, 30, 501.0, 502.0, 502.5, 500.8),
            _bar(12, 0, 502.0, 503.0, 503.2, 501.9)]
    assert decide(bars, PDH)["state"] == "waiting"


def test_book_isolation_by_signature():
    from analysis import day_bias
    src = inspect.getsource(day_bias)
    assert BOOK == "day_bias"
    assert "book='swing'" not in src and "book='gamma" not in src
    # Every spec/trade statement is scoped to this book's rows.
    assert "WHERE book=%s" in src or "book=%s" in src
    # Isolation must be SYMMETRIC (2026-08-25, trade #70): the main
    # trigger loop grabbed the day-bias spec and filled it by the
    # gamma book's close_through rules — before 10:30, without a
    # touch. The day-bias book is managed ONLY by its own loop.
    from analysis import paper_trader
    loop_src = inspect.getsource(paper_trader.run_trigger_loop)
    assert "day_bias" in loop_src, \
        "run_trigger_loop must exclude the day_bias book"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
