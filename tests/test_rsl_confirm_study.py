"""The confirmed-runner cut (2026-09-03 AM), pinned:
  1. legs_at reads completed bars only: or15 needs 15 bars, hh5 needs
     two completed 5m blocks before the GO's block, warmups are None.
  2. The option model: a 0.70-delta strike sits below spot; a flat
     stock loses money to decay; a Friday GO expires at the close.
  3. Writes only rsl_confirm_events; trend gate imported, not copied.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import rsl_confirm_study as rc  # noqa: E402


def _bars(specs, t0=dt.datetime(2026, 9, 2, 9, 30)):
    return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]


def test_legs_read_completed_bars_only():
    # 9:30-9:44 range 100-101; block 9:35 high 101 = session high so far;
    # block 9:40 high 101.5 (new high); GO at 9:47 closes 101.8 above OR high
    specs = [(100, 100.8, 99.9, 100.5, 1000)] * 5
    specs += [(100.5, 101.0, 100.4, 100.9, 1000)] * 5
    specs += [(100.9, 101.5, 100.8, 101.4, 1000)] * 5
    specs += [(101.4, 101.9, 101.3, 101.8, 1000)] * 3
    b = _bars(specs)
    legs = rc.legs_at(b, 17, 1.2)
    assert legs["or15_break"] is True and legs["hh5"] is True
    assert legs["above_vwap"] is True and legs["late"] is False
    assert legs["rs_strong"] is True
    assert legs["trend1m_on"] is None            # inside the 21-bar warmup
    # too early for the OR and hh5 legs -> None, never False
    early = rc.legs_at(b[:8], 7, 0.5)
    assert early["or15_break"] is None and early["hh5"] is None
    assert early["rs_strong"] is False
    late = rc.legs_at(_bars(specs, dt.datetime(2026, 9, 2, 9, 50)), 17, None)
    assert late["late"] is True and late["rs_strong"] is None


def test_option_model_semantics():
    ts = dt.datetime(2026, 9, 2, 9, 47)              # Wednesday
    m = rc.option_model(100.0, 100.0, ts, 0.35)
    assert m["strike"] < 100.0                       # ITM for delta 0.70
    assert m["opt_pnl"] < 0 and m["shares_eq"] == 0  # flat stock, pure decay
    up = rc.option_model(100.0, 101.0, ts, 0.35)
    assert 0 < up["opt_pnl"] < up["shares_eq"] + 1   # drag vs shares-equivalent
    fri = rc.option_model(100.0, 100.0, dt.datetime(2026, 9, 4, 9, 47), 0.35)
    assert fri["t_go_days"] < 1
    assert abs(fri["prem_close"] - (100.0 - fri["strike"])) < 0.01   # intrinsic at expiry
    assert rc.option_model(100.0, 101.0, ts, None) is None
    assert rc.realized_vol([100.0] * 10) is None     # < 15 returns = hole
    assert abs(rc.bs_call(100, 90, 0.0, 0.3) - 10.0) < 1e-9


def test_scope():
    src = inspect.getsource(rc)
    assert "from analysis.rankladder_study import trend1m_at" in src
    assert "INSERT INTO rsl_confirm_events" in src
    assert "entry_kind='go_pullback'" in src
    for forbidden in ("paper_trades", "paper_specs", "rsl_book_bars",
                      "def trend1m_at", "def trend_series"):
        assert forbidden not in src
    sched = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "alerts", "scheduler.py")).read()
    assert '("rsl_confirm", "analysis.rsl_confirm_study")' in sched


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
