"""Beat-SPY harness (2026-09-07): the pure rules, the once-only sealed
run, and the simulator on a tiny synthetic market."""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import beat_spy as bs  # noqa: E402


def test_pure_rules():
    closes = [100 + i for i in range(300)]
    assert bs.momentum(closes, 299, 126, 21) is not None and bs.momentum(closes, 100, 126, 21) is None
    assert bs.rank_pool({"A": 0.1, "B": 0.3, "C": None}) == ["B", "A"]
    assert bs.target_holdings(["A", "B", "C", "D", "E"], ["D", "Z"], 2) == ["D", "A"]   # D kept (inside top 4), Z dropped
    assert bs.target_holdings(["A", "B", "C", "D", "E"], ["E"], 2) == ["A", "B"]         # E outside top 4 -> rotated out
    assert bs.ladder_fills(10.0, [9.0, 8.4, 7.4], [9.5, 8.6, 7.6], (0.0, -0.15, -0.25)) == [10.0, 8.5, 7.5]
    assert bs.ladder_fills(10.0, [9.9, 9.8], [9.9, 9.8], (0.0, -0.15, -0.25)) == [10.0, None, None]
    assert abs(bs.max_drawdown([100, 120, 90, 130]) - (-0.25)) < 1e-9
    assert bs.sma([1, 2, 3, 4], 4, 3) == 2.5 and bs.sma([1, 2, 3], 4, 2) is None


def _series(dates, closes):
    return dict(dates=dates, highs=[c * 1.01 for c in closes], lows=[c * 0.99 for c in closes],
                closes=closes, idx={d: i for i, d in enumerate(dates)})


def test_simulate_tracks_spy_when_only_spy_qualifies():
    d0 = dt.date(2010, 1, 4)
    dates = []
    d = d0
    while len(dates) < 700:
        if d.weekday() < 5:
            dates.append(d)
        d += dt.timedelta(days=1)
    spy = [100 * (1.0005 ** i) for i in range(700)]           # steady uptrend
    px = {"SPY": _series(dates, spy), "TLT": _series(dates, [100.0] * 700), "GLD": _series(dates, [100.0] * 700)}
    p = dict(bs.DEFAULTS)
    p.update(start=dates[300], end=dates[-1], top_n=1, a_weight=1.0, b_weight=0.0, b_slots=0)
    res = bs.simulate(px, [], {}, p)
    st = res["stats"]
    assert st["n_trades"] >= 1 and st["final_equity"] > 100_000
    # SPY-only system with costs should land just under SPY price return
    assert 0.97 < st["final_equity"] / st["spy_final"] <= 1.0
    assert st["max_dd"] <= 0.0 and st["spy_max_dd"] <= 0.0


def test_sealed_runs_once_and_scope():
    src = inspect.getsource(bs.run_variant)
    assert "refusing to re-run" in src and 'window == "sealed"' in src
    assert bs.SEALED_START == dt.date(2024, 1, 2) and bs.SEALED_END == dt.date(2026, 9, 4)
    assert bs.BUILD_END == dt.date(2023, 12, 31) and bs.COST_BPS == 5.0
    whole = inspect.getsource(bs)
    for forbidden in ("INSERT INTO paper_", "UPDATE paper_", "trade_journal", "DELETE FROM daily_prices"):
        assert forbidden not in whole
    assert "ON CONFLICT (ticker, trade_date) DO NOTHING" in whole      # backfill never overwrites


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
