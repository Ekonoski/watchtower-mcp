"""The RS-leader exit re-grade (2026-09-02 evening), pinned:
  1. lifecycle_state's DEFAULT behaviour is unchanged (the live book);
     the declared switches do exactly one thing each — struct_stop=False
     ignores a 5m close through the stop, trail=False ignores the trail,
     arm_px overrides the +1R switch — and the disaster touch is never
     switchable.
  2. sim_variants: 'hold' is the bell close with no rule; 'disaster'
     survives a 5m close through the struct stop; 'struct_bell' stops
     there; 'wide5' sits under the five-minute window's low; every variant reports
     r_go on the GO risk unit.
  3. Writes only rsl_exit_events; grades the go_pullback population
     only (the chase study's first pass had let the no-pullback rows in).
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import rsl_exit_study as rx  # noqa: E402
from analysis.rs_leader_book import lifecycle_state  # noqa: E402


def _bars(specs, t0=dt.datetime(2026, 9, 2, 9, 45)):
    return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]


def test_switches_do_one_thing_each():
    entry, stop = 100.0, 99.5                      # risk 0.5, disaster 99.0
    # GO at idx 0 (9:45); the 9:45-9:49 block closes at 99.3 (< stop, > disaster)
    specs = [(100, 100.1, 99.9, 100.0)] + [(99.6, 99.7, 99.25, 99.3)] * 4
    specs += [(99.3, 99.4, 99.2, 99.3)] * 10       # keeps closing under the stop
    b = _bars(specs)
    assert lifecycle_state(b, 0, entry, stop)["exit"][0] == "stop"        # live
    assert lifecycle_state(b, 0, entry, stop, struct_stop=False)["exit"] is None
    assert lifecycle_state(b, 0, entry, stop, trail=False)["exit"][0] == "stop"
    # trail switch: arm, then a 5m close under the EMA AND under the stop
    specs2 = [(100, 100.1, 99.9, 100.0)] + [(100.4, 100.6, 100.3, 100.5)] * 4
    specs2 += [(100.5, 100.6, 100.4, 100.5)] * 5 + [(99.5, 99.55, 99.3, 99.4)] * 5
    b2 = _bars(specs2)
    assert lifecycle_state(b2, 0, entry, stop)["exit"][0] == "trail"
    # armed + no trail: the struct stop is retired once armed, so it rides
    assert lifecycle_state(b2, 0, entry, stop, trail=False)["exit"] is None
    # arm_px override: a higher switch never arms on this tape -> stop, not trail
    assert lifecycle_state(b2, 0, entry, stop, arm_px=105.0)["exit"][0] == "stop"
    # the disaster touch is not switchable
    specs3 = [(100, 100.1, 99.9, 100.0)] + [(99.5, 99.6, 98.9, 99.4)] * 4
    b3 = _bars(specs3)
    assert lifecycle_state(b3, 0, entry, stop, trail=False,
                           struct_stop=False)["exit"][0] == "disaster"
    sig = inspect.signature(lifecycle_state)
    assert "disaster" not in sig.parameters


def test_variant_semantics():
    entry, stop = 100.0, 99.5
    # four pullback bars (lows 99.3), GO at idx 4 (9:49), then the 9:50 block
    # CLOSES 99.3 (through the 99.5 stop, above wide5's 99.25 and the 99.0
    # disaster), then a full recovery to 101 by the bell
    specs = [(99.8, 99.9, 99.3, 99.7)] * 4 + [(100, 100.1, 99.9, 100.0)]
    specs += [(99.6, 99.7, 99.28, 99.3)] * 5
    specs += [(100.5, 101.2, 100.4, 101.0)] * 10
    b = _bars(specs)
    out = rx.sim_variants(b, 4, entry, stop)
    assert set(out) == set(rx.VARIANTS)
    assert out["hold"]["out"] == "eod" and out["hold"]["r_go"] == 2.0
    assert out["disaster"]["out"] == "eod"                   # survived the close-through
    assert out["struct_bell"]["out"] == "stop" and out["struct_bell"]["r_go"] < 0
    assert out["book"]["out"] == "stop"
    assert out["dis_trail"]["out"] == "eod"                  # no struct stop; +1R touched, EMA held
    assert abs(out["wide5"]["stop"] - 99.3 * (1 - 0.0005)) < 1e-3   # the window low (stored to 4dp)
    assert out["wide5"]["r_own"] < out["wide5"]["r_go"]      # wider risk -> smaller own-R
    assert out["wide5"]["out"] == "eod"                      # 99.3 close did not breach 99.25


def test_scope_and_signature():
    src = inspect.getsource(rx)
    assert "from analysis.rs_leader_book import lifecycle_state" in src
    assert "INSERT INTO rsl_exit_events" in src
    assert "entry_kind='go_pullback'" in src
    for forbidden in ("paper_trades", "paper_specs", "rsl_book_bars", "_res5",
                      "e21_by_min"):
        assert forbidden not in src
    from analysis import chase_study
    assert "entry_kind='go_pullback'" in inspect.getsource(chase_study)
    sched = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "alerts", "scheduler.py")).read()
    assert '("rsl_exit", "analysis.rsl_exit_study")' in sched


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
