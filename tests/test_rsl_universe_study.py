"""The leader-board seat test (2026-09-08), pinned: one definition (the
universe study IMPORTS the graded study's rank / entry / sim and
restates none of them), explicit universes (ETFs can never be ranked),
the control universe IS the mag-7, and writes-own-table by signature."""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import rsl_universe_study as u  # noqa: E402
from analysis import rsleader_study as base  # noqa: E402


def test_universes_are_explicit_and_the_control_is_the_mag7():
    us = u.universes()
    assert us["mag7"] == tuple(base.TICKERS)
    assert set(u.CANDIDATES) == {"AMD", "AVGO", "PLTR", "MU", "NFLX"}
    for x in u.CANDIDATES:
        assert us[f"mag7+{x}"] == tuple(base.TICKERS) + (x,)
    assert len(us["mag12"]) == 12 and set(us["mag12"]) == set(base.TICKERS) | set(u.CANDIDATES)
    for members in us.values():                      # index ETFs are never ranked
        assert not ({"SPY", "QQQ", "IWM"} & set(members))
    assert len(us) == 7


def _bars(n, o=100.0, step=0.0):
    out = []
    for i in range(n):
        px = o + step * i
        out.append((dt.datetime(2026, 9, 8, 9, 30) + dt.timedelta(minutes=i), px, px + 0.1, px - 0.1, px))
    return out


def test_rets_and_grade_use_the_studys_definitions():
    day = {"A": _bars(130, step=0.01), "B": _bars(130), "C": _bars(130), "D": _bars(130), "E": _bars(100)}
    rets = u.rets_at_945(day)
    assert "E" not in rets                            # too few bars: excluded, not zero
    assert abs(rets["A"] - (100.14 / 100 - 1) * 100) < 1e-9 and rets["B"] == 0.0
    # fewer than five ranked names → no rows (the study's own floor)
    assert u.grade_universe("x", ("A", "B", "C"), day, 0.0, dt.date(2026, 9, 8)) == []
    src = inspect.getsource(u)
    assert "from analysis.rsleader_study import" in src
    for fn in ("rs_rank", "find_go_entry", "sim_bracket", "ema"):
        assert f"def {fn}" not in src, fn             # imported, never reimplemented


def test_bar_is_frozen_and_writes_only_its_own_table():
    src = inspect.getsource(u)
    assert "INSERT INTO rsl_universe_events" in src
    for forbidden in ("INSERT INTO paper_", "UPDATE ", "DELETE FROM", "INSERT INTO rs_leader_events"):
        assert forbidden not in src, forbidden
    assert u.MIN_N_HALF == 15 and u.HALF_SPLIT == dt.date(2025, 9, 1)
    assert "POSITIVE IN BOTH YEAR-HALVES" in src and "must\nnot dilute" in src or "not dilute the board" in src
    assert "LEAST(GREATEST(r_close,-10),10)" in u.READOUT_SQL   # the ±10R cap on every aggregate


def test_backfill_v2_names_and_the_daily_owner():
    from analysis import index_bars_daily, liquid_bars
    assert liquid_bars.V2_TICKERS == ("AVGO", "PLTR", "MU", "NFLX")
    assert liquid_bars.V2_MARKER == u.BARS_MARKER
    assert set(liquid_bars.V2_TICKERS) <= set(index_bars_daily.LIQUID)   # every stored record has an owner


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
