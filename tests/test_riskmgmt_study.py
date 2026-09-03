"""Risk management as the edge (2026-09-03 AM), pinned:
  1. Every variant's R is on the 1% unit; 'hold' is the bell; 'dis1'
     exits only on the 1% touch; the dt_* variants carry no struct stop
     and arm the trail at their declared level.
  2. Long only; writes riskmgmt_events only; the lifecycle is imported.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import riskmgmt_study as rm  # noqa: E402


def _bars(specs, t0=dt.datetime(2026, 9, 2, 9, 45)):
    return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]


def test_variant_semantics():
    entry = 100.0
    # dip: a 5m block closes -0.6% (no touch of -1%), then +1.2% run, then
    # a 5m close back under the (lagging) EMA at -0.5%, above the -1% line
    specs = [(100, 100.1, 99.9, 100.0)] + [(99.6, 99.7, 99.35, 99.4)] * 4
    specs += [(100.8, 101.2, 100.7, 101.1)] * 10 + [(99.6, 99.65, 99.4, 99.5)] * 5
    out = rm.sim_variants(_bars(specs), 0, entry)
    assert set(out) == {"hold", "dis1", "dt_050", "dt_100", "dt_150"}
    assert out["hold"]["out"] == "eod" and abs(out["hold"]["r"] + 0.5) < 1e-9
    assert out["dis1"]["out"] == "eod"                 # -0.6% never touched -1%
    assert out["dt_050"]["out"] == "trail" and out["dt_100"]["out"] == "trail"
    assert out["dt_150"]["out"] == "eod"               # +1.2% never armed +1.5%
    assert out["dt_100"]["r"] == out["dt_050"]["r"]    # same exit bar, same unit
    # a -1% touch is the only stop in every variant
    knife = _bars([(100, 100.1, 99.9, 100.0)] + [(99.5, 99.6, 98.95, 99.4)] * 4)
    k = rm.sim_variants(knife, 0, entry)
    assert all(k[v]["out"] == "disaster" for v in ("dis1", "dt_050", "dt_100", "dt_150"))
    assert abs(k["dis1"]["r"] + 1.0) < 1e-9            # the disaster IS -1R


def test_scope():
    src = inspect.getsource(rm)
    assert "from analysis.rs_leader_book import lifecycle_state" in src
    assert "INSERT INTO riskmgmt_events" in src and "direction='long'" in src
    for forbidden in ("paper_trades", "paper_specs", "rsl_book_bars", "_res5", "e21_by_min"):
        assert forbidden not in src
    sched = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "alerts", "scheduler.py")).read()
    assert '("riskmgmt", "analysis.riskmgmt_study")' in sched


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
