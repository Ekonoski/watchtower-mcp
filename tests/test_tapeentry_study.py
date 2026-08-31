"""The tape-entry study's pure cores, pinned (2026-09-02).

  1. resample5 is fixed-anchor from 9:30 — bucket membership never
     depends on where the series ends (no repaint).
  2. The wick rule: an EMA touch that closes failing never enters.
  3. level_machine: the ORB break can be the first post-ORB bar
     (start_inside), PDH needs a close inside first, and a close back
     through the level before the retest kills the setup.
  4. sim_stops: touch stops exit at the stop price; close-rule stops
     ignore the wick and exit at the offending 5m CLOSE; ema21x
     carries r=None (no fixed risk unit).
  5. Writes only its own tables — by signature.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.tapeentry_study import (ema_retest_entry,  # noqa: E402
                                      level_machine, resample5, sim_stops)


def _b(minute, o, h, l, c, hour=9, base_min=30):
    ts = dt.datetime(2026, 9, 2, hour, base_min) + dt.timedelta(minutes=minute)
    return (ts, o, h, l, c)


def test_resample5_is_fixed_anchor():
    bars = [_b(i, 100 + i, 100.5 + i, 99.5 + i, 100.2 + i) for i in range(12)]
    full, last_full = resample5(bars)
    trunc, _ = resample5(bars[:11])
    assert len(full) == 3                       # 9:30-35, 35-40, 40-42 stub
    assert full[0][1] == 100 and full[0][4] == 100.2 + 4
    # the completed buckets are identical no matter where the series ends
    assert full[0] == trunc[0] and full[1] == trunc[1]
    assert last_full[0] == 4 and last_full[1] == 9


def test_wick_rule_refuses_failing_close():
    bar = _b(0, 100.3, 100.4, 99.9, 100.2)
    assert ema_retest_entry(bar, 100.0, 1, "long") == 100.2
    failing = _b(0, 100.3, 100.4, 99.9, 99.95)   # touched, closed below
    assert ema_retest_entry(failing, 100.0, 1, "long") is None
    assert ema_retest_entry(bar, 100.0, 0, "long") is None   # no trend, no entry


def test_level_machine_orb_and_kill():
    lvl = 100.0
    up = [_b(i * 5, 99.5, 99.9, 99.0, 99.5) for i in range(2)]
    up += [_b(10, 99.8, 100.4, 99.7, 100.3),     # break: close through
           _b(15, 100.3, 100.5, 99.9, 100.2)]    # retest: touch, close holds
    brk, rt = level_machine(up, lvl, "long", 0, 9, start_inside=True)
    assert brk == 2 and rt == 3
    # ORB: the FIRST bar can be the break (the range is already inside)
    fast = [_b(0, 99.8, 100.4, 99.7, 100.3), _b(5, 100.3, 100.5, 99.9, 100.2)]
    brk, rt = level_machine(fast, lvl, "long", 0, 9, start_inside=True)
    assert brk == 0 and rt == 1
    # PDH: without start_inside the same tape has no break to buy
    brk, rt = level_machine(fast, lvl, "long", 0, 9, start_inside=False)
    assert brk is None and rt is None
    # a close back through before the retest kills the setup
    dead = [_b(0, 99.5, 99.9, 99.0, 99.5),
            _b(5, 99.8, 100.4, 99.7, 100.3),
            _b(10, 100.2, 100.3, 99.5, 99.7),    # close back below
            _b(15, 100.0, 100.2, 99.9, 100.1)]
    brk, rt = level_machine(dead, lvl, "long", 0, 9, start_inside=True)
    assert brk == 1 and rt is None


def test_stops_touch_vs_close_rule():
    entry, struct = 100.0, 99.5
    bars1 = [_b(0, 100, 100.1, 99.9, 100),
             _b(1, 100, 100.1, 99.45, 99.9),     # wick through struct
             _b(2, 99.9, 100.6, 99.8, 100.5)]
    bars5 = [(_b(0, 100, 100.6, 99.45, 100.5)[0], 100, 100.6, 99.45, 100.5)]
    e21 = [98.0]
    eod, mfe, mae, stops = sim_stops(bars1, 1, entry, "long", struct, 0.4,
                                     bars5, 1, e21)
    assert stops["struct"]["out"] == "stopped"
    assert stops["struct"]["exit_px"] == 99.5    # exits AT the stop price
    assert stops["struct_5c"]["out"] == "eod"    # the 5m CLOSE held — no exit
    assert stops["ema21x"]["out"] == "eod" and stops["ema21x"]["r"] is None
    assert stops["pct100"]["out"] == "eod"
    assert eod == 50.0 and mae < 0 < mfe
    # close-rule fires on a completed close through, at that close
    bars5b = [(bars5[0][0], 100, 100.1, 99.0, 99.2)]
    bars1b = [_b(0, 100, 100.1, 99.9, 100), _b(1, 100, 100.1, 99.0, 99.2)]
    _, _, _, st2 = sim_stops(bars1b, 1, entry, "long", struct, 0.4,
                             bars5b, 0, [98.0])
    assert st2["struct_5c"]["out"] == "stopped"
    assert st2["struct_5c"]["exit_px"] == 99.2   # the close, not the level


def test_writes_only_its_own_tables():
    from analysis import tapeentry_study
    src = inspect.getsource(tapeentry_study)
    assert "INSERT INTO tapeentry_events" in src
    assert "INSERT INTO tapeentry_days" in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
