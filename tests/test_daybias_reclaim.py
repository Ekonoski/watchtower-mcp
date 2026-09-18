"""The day-bias early-touch-RECLAIM study's event definition, pinned
(2026-09-18, the day day_bias stopped counting as a book).

  1. A cancelled_early day whose level was LOST on a close and regained
     on a >=10:30 close is a 'reclaimed' event with lost_close=True; the
     entry is the reclaim bar's CLOSE, outcomes run to the day's close.
  2. A wick touch that never closed below PDH still yields the first
     >=10:30 close above PDH, but lost_close=False — recorded, cut apart
     at readout, never merged.
  3. A close above PDH BEFORE 10:30 is not a reclaim (the cancel window
     is the coin-flip bucket the book refuses; the study honours it).
  4. No >=10:30 close above PDH -> 'no_reclaim', its own state.
  5. The 0.75% stop variant decides on 15m CLOSES only — a wick through
     is not a stop (wick rule).
  6. A day the book would not have cancelled (no_bias / filled) is None.
  7. One definition: the day is classified by day_bias.decide, imported;
     the module writes only its own table.

Standalone:  python3 tests/test_daybias_reclaim.py
"""
import datetime as dt
import inspect
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import daybias_reclaim_study as st  # noqa: E402

ET = ZoneInfo("America/New_York")
PDH = 100.0


def _bar(h, m, o, c, hi, lo):
    return (dt.datetime(2026, 8, 25, h, m, tzinfo=ET), o, c, hi, lo, 1000.0)


def _day(after_touch):
    """9:30 opens above PDH; 9:45 touches (low 99.9) — the book cancels."""
    return [_bar(9, 30, 100.5, 100.8, 101.0, 100.3),
            _bar(9, 45, 100.8, 99.8, 100.9, 99.9)] + after_touch


def test_lost_then_reclaimed_after_1030():
    bars = _day([_bar(10, 0, 99.8, 99.9, 100.05, 99.7),
                 _bar(10, 15, 99.9, 99.95, 100.1, 99.8),
                 _bar(10, 30, 99.95, 100.2, 100.3, 99.9),   # reclaim: close > PDH
                 _bar(10, 45, 100.2, 100.6, 100.9, 100.1),
                 _bar(15, 45, 100.6, 101.2, 101.3, 100.5)])
    ev = st.reclaim(bars, PDH)
    assert ev["state"] == "reclaimed" and ev["lost_close"] is True
    assert ev["reclaim_ts"].time() == dt.time(10, 30)
    assert ev["entry_px"] == 100.2 and ev["day_close_px"] == 101.2
    assert abs(ev["eod_bps"] - (101.2 / 100.2 - 1) * 1e4) < 0.01
    assert ev["stop_hit"] is False and ev["stop_exit_px"] == 101.2
    assert ev["mfe_bps"] > 0 and ev["mae_bps"] <= 0
    # the official daily close overrides the last bar when supplied
    ev2 = st.reclaim(bars, PDH, day_close=101.0)
    assert ev2["day_close_px"] == 101.0


def test_wick_touch_never_lost_is_recorded_apart():
    bars = _day([_bar(10, 0, 100.1, 100.3, 100.4, 100.0),
                 _bar(10, 30, 100.3, 100.5, 100.6, 100.2),
                 _bar(15, 45, 100.5, 100.9, 101.0, 100.4)])
    # the 9:45 touch bar closed 99.8 in _day — make it a wick instead
    bars[1] = _bar(9, 45, 100.8, 100.2, 100.9, 99.9)
    ev = st.reclaim(bars, PDH)
    assert ev["state"] == "reclaimed" and ev["lost_close"] is False
    assert ev["reclaim_ts"].time() == dt.time(10, 30)


def test_pre_1030_close_above_pdh_is_not_the_reclaim():
    bars = _day([_bar(10, 0, 99.8, 100.4, 100.5, 99.7),    # early close above: not it
                 _bar(10, 15, 100.4, 99.9, 100.5, 99.8),    # lost again
                 _bar(10, 30, 99.9, 99.95, 100.0, 99.8),
                 _bar(10, 45, 99.95, 100.3, 100.4, 99.9),   # the event
                 _bar(15, 45, 100.3, 100.1, 100.6, 100.0)])
    ev = st.reclaim(bars, PDH)
    assert ev["reclaim_ts"].time() == dt.time(10, 45) and ev["entry_px"] == 100.3
    assert ev["lost_close"] is True


def test_no_reclaim_is_its_own_state():
    bars = _day([_bar(10, 0, 99.8, 99.6, 99.9, 99.5),
                 _bar(10, 30, 99.6, 99.7, 100.0, 99.4),     # wick to PDH, close under
                 _bar(15, 45, 99.7, 99.2, 99.8, 99.0)])
    ev = st.reclaim(bars, PDH)
    assert ev["state"] == "no_reclaim" and ev["touch_ts"].time() == dt.time(9, 45)
    assert "eod_bps" not in ev


def test_stop_variant_is_close_rule():
    stop = 100.2 * (1 - st.DISASTER_STOP_PCT)          # 99.4485
    base = _day([_bar(10, 30, 99.9, 100.2, 100.3, 99.8)])
    wick = base + [_bar(10, 45, 100.2, 99.6, 100.3, 99.3),   # low through, close above
                   _bar(15, 45, 99.6, 100.9, 101.0, 99.5)]
    ev = st.reclaim(wick, PDH)
    assert ev["stop_hit"] is False and ev["stop_exit_px"] == 100.9
    closed = base + [_bar(10, 45, 100.2, 99.4, 100.3, 99.3),  # close through
                     _bar(15, 45, 99.4, 100.9, 101.0, 99.3)]
    ev = st.reclaim(closed, PDH)
    assert ev["stop_hit"] is True and ev["stop_exit_px"] == 99.4 < stop
    assert ev["stop_r"] < -1.0 and ev["eod_bps"] > 0     # hold would have won: both stated


def test_not_a_cancelled_day_is_none():
    no_bias = [_bar(9, 30, 99.5, 100.5, 100.8, 99.4), _bar(10, 30, 100.5, 100.8, 101.0, 100.4)]
    assert st.reclaim(no_bias, PDH) is None
    late_fill = [_bar(9, 30, 100.5, 100.8, 101.0, 100.3),
                 _bar(10, 30, 100.8, 100.4, 100.9, 99.9)]      # the book's own fill
    assert st.reclaim(late_fill, PDH) is None


def test_one_definition_and_write_scope():
    src = inspect.getsource(st)
    assert "from analysis.day_bias import" in src and "decide" in src
    assert "def decide(" not in src
    assert "INSERT INTO daybias_reclaim_events" in src
    for forbidden in ("paper_trades", "paper_specs", "UPDATE ", "DELETE "):
        assert forbidden not in src, forbidden
    assert "conn" not in inspect.signature(st.reclaim).parameters


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
