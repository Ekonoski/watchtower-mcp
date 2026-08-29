"""The full-stack dot study's pure legs, pinned (2026-08-29).

  1. Each leg is Eric's spec verbatim: %R floored (<= -80 recently)
     AND rising; RSI <= 50 AND rising; MACD line below zero with the
     histogram rising ("below the zero line and curving up").
  2. A hole in any input fails the leg (False, never a guess).
  3. The shelf proxy counts time-at-price, prior days only.
  4. Writes only greendot_stack — by signature.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_stack import (shelf_days,  # noqa: E402
                                     stack_legs)


def test_full_stack_fires_on_the_achr_shape():
    # %R floored at -90 two bars ago, rising; RSI 44 rising; MACD -0.5
    # with histogram red but shrinking. All three legs true.
    cur = dict(pctr=-72.0, rsi=44.0, macd=-0.5, macd_hist=-0.20)
    prev = dict(pctr=-85.0, rsi=41.0, macd=-0.6, macd_hist=-0.40)
    legs = stack_legs(cur, prev, pctr_recent_min=-90.0)
    assert legs == {"pctr_turn": True, "rsi_turn": True,
                    "macd_turn": True}


def test_no_floor_no_pctr_leg():
    cur = dict(pctr=-40.0, rsi=44.0, macd=-0.5, macd_hist=-0.2)
    prev = dict(pctr=-50.0, rsi=41.0, macd=-0.6, macd_hist=-0.4)
    legs = stack_legs(cur, prev, pctr_recent_min=-60.0)  # never <= -80
    assert legs["pctr_turn"] is False


def test_recovered_rsi_fails_the_turn_leg():
    cur = dict(pctr=-72.0, rsi=61.0, macd=-0.5, macd_hist=-0.2)
    prev = dict(pctr=-85.0, rsi=55.0, macd=-0.6, macd_hist=-0.4)
    legs = stack_legs(cur, prev, pctr_recent_min=-90.0)
    assert legs["rsi_turn"] is False       # recovered isn't turning


def test_macd_above_zero_fails_the_leg():
    cur = dict(pctr=-72.0, rsi=44.0, macd=0.3, macd_hist=0.1)
    prev = dict(pctr=-85.0, rsi=41.0, macd=0.2, macd_hist=0.05)
    legs = stack_legs(cur, prev, pctr_recent_min=-90.0)
    assert legs["macd_turn"] is False      # "below the zero line" is a leg


def test_holes_fail_legs_never_guess():
    cur = dict(pctr=None, rsi=44.0, macd=None, macd_hist=-0.2)
    prev = dict(pctr=-85.0, rsi=None, macd=-0.6, macd_hist=None)
    legs = stack_legs(cur, prev, pctr_recent_min=None)
    assert legs == {"pctr_turn": False, "rsi_turn": False,
                    "macd_turn": False}


def test_shelf_counts_prior_days_only():
    closes = [10.0] * 30 + [8.0] * 5 + [10.0]
    # Dot at the last index, px 10: the 30 old days at 10.0 count,
    # the 8.0 dip doesn't, and the dot day itself is excluded.
    assert shelf_days(closes, len(closes) - 1, 10.0) == 30


def test_writes_only_its_own_table():
    from analysis import greendot_stack
    src = inspect.getsource(greendot_stack)
    assert "INSERT INTO greendot_stack" in src
    assert "INSERT INTO greendot_dots" not in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
