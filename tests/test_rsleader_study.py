"""The RS-leader study's pure core, pinned (2026-09-01).

  1. Leader/laggard rank vs QQQ with the +-0.4% bar; no qualifier ->
     None, never a forced pick.
  2. The GO entry demands a CLOSE holding the EMA (wick rule); a wick
     through with a failing close never enters.
  3. Bracket sim: same-bar stop+target = STOPPED (conservative).
  4. Writes only rs_leader_events — by signature.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.rsleader_study import (find_go_entry, rs_rank,  # noqa: E402
                                     sim_bracket)


def test_leader_and_laggard_need_the_bar():
    rets = {"TSLA": 1.4, "NVDA": 0.2, "AAPL": 0.1, "MSFT": 0.0,
            "AMZN": -0.1, "GOOGL": -0.2, "META": -0.9}
    leader, laggard, midpack, rs = rs_rank(rets, qqq_ret=0.1)
    assert leader == "TSLA" and laggard == "META"
    assert midpack == "MSFT"
    flat = {t: 0.1 for t in rets}
    leader2, laggard2, _, _ = rs_rank(flat, qqq_ret=0.1)
    assert leader2 is None and laggard2 is None


def _ts(i):
    return dt.datetime(2026, 9, 1, 9, 45) + dt.timedelta(minutes=i)


def test_go_entry_needs_the_holding_close():
    e8 = [100.0] * 6
    e21 = [99.5] * 6
    bars = [(_ts(i), 100.4, 100.5, 100.3, 100.4) for i in range(6)]
    bars[3] = (_ts(3), 100.3, 100.4, 99.95, 100.2)   # touch 8, close above
    got = find_go_entry(bars, e8, e21, 0, 6, "long")
    assert got is not None and got[0] == 3 and got[1] == 100.2
    bars[3] = (_ts(3), 100.3, 100.4, 99.9, 99.95)    # touch, close BELOW
    assert find_go_entry(bars, e8, e21, 0, 4, "long") is None


def test_same_bar_both_touch_is_stopped():
    bars = [(_ts(0), 100, 100.1, 99.9, 100),
            (_ts(1), 100, 103.0, 98.0, 100)]         # hits 2R AND stop
    out = sim_bracket(bars, 0, 100.0, 99.0, "long")
    assert out[0] == "stopped" and out[1] == -1.0


def test_target2_pays_two_r():
    bars = [(_ts(0), 100, 100.1, 99.9, 100),
            (_ts(1), 100.5, 102.2, 100.4, 102.0)]
    out = sim_bracket(bars, 0, 100.0, 99.0, "long")
    assert out[0] == "target2" and out[1] == 2.0


def test_writes_only_its_own_table():
    from analysis import rsleader_study
    src = inspect.getsource(rsleader_study)
    assert "INSERT INTO rs_leader_events" in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
