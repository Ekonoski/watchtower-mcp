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


def test_v2_regime_and_no_forced_liquidation():
    dates = [dt.date(2020, 1, 30), dt.date(2020, 1, 31), dt.date(2020, 2, 3), dt.date(2020, 2, 28), dt.date(2020, 3, 2)]
    assert bs.month_ends(dates) == [1, 3, 4]
    # a holding above its own 200d in a risk-off month is KEPT under v2 and SOLD under v1
    d0 = dt.date(2010, 1, 4)
    cal = []
    d = d0
    while len(cal) < 900:
        if d.weekday() < 5:
            cal.append(d)
        d += dt.timedelta(days=1)
    # SPY: rises 600 bars, then collapses 40% over 100 bars and stays (risk-off); XLK keeps rising
    spy = [100 * (1.001 ** i) for i in range(600)] + [100 * (1.001 ** 600) * (1 - 0.004 * k) for k in range(1, 101)]
    spy += [spy[-1]] * (900 - len(spy))
    xlk = [50 * (1.0012 ** i) for i in range(900)]
    px = {"SPY": _series(cal, spy), "XLK": _series(cal, xlk),
          "TLT": _series(cal, [100.0] * 900), "GLD": _series(cal, [100.0] * 900)}
    base = dict(bs.DEFAULTS)
    base.update(start=cal[300], end=cal[-1], top_n=1, a_weight=1.0, b_weight=0.0, b_slots=0)
    v1 = bs.simulate(px, [], {}, dict(base, regime="weekly200", force_liquidate=True))
    v2 = bs.simulate(px, [], {}, dict(base, regime="faber", force_liquidate=False))
    assert any(t["reason"] == "risk_off" for t in v1["trades"])          # v1 dumped XLK when SPY broke
    assert not any(t["reason"] == "risk_off" for t in v2["trades"])      # v2 held it (above its own 200d)
    assert v2["stats"]["final_equity"] > v1["stats"]["final_equity"]


def test_option_model_and_overlay():
    # deep-ITM call: ~0.80 delta strike sits below spot; value at expiry is intrinsic
    K = bs.strike_for_delta(100.0, bs.CALL_TENOR, 0.25)
    assert 80.0 < K < 97.0
    assert abs(bs.bs_call(120.0, K, 0.0, 0.25) - (120.0 - K)) < 1e-9
    assert bs.bs_call(100.0, K, bs.CALL_TENOR, 0.25) > 100.0 - K          # extrinsic > 0 before expiry
    # the overlay on a rising synthetic: levered run beats shares, premium never breaches the cap
    d0 = dt.date(2010, 1, 4)
    cal = []
    d = d0
    while len(cal) < 900:
        if d.weekday() < 5:
            cal.append(d)
        d += dt.timedelta(days=1)
    import random
    rng = random.Random(7)
    spy = [100.0]
    for _ in range(899):
        spy.append(spy[-1] * (1 + 0.0006 + rng.gauss(0, 0.01)))
    px = {"SPY": _series(cal, spy), "TLT": _series(cal, [100.0] * 900), "GLD": _series(cal, [100.0] * 900)}
    base = dict(bs.DEFAULTS)
    base.update(start=cal[300], end=cal[-1], top_n=1, a_weight=1.0, b_weight=0.0, b_slots=0,
                regime="none", force_liquidate=False, pool="core")
    sh = bs.simulate(px, [], {}, dict(base, lev=1.0))
    cl = bs.simulate(px, [], {}, dict(base, lev=1.5))
    assert any(t["reason"].endswith("_call") for t in cl["trades"])
    assert cl["stats"]["final_equity"] != sh["stats"]["final_equity"]
    assert bs.PREMIUM_CAP == 0.25 and bs.CALL_SPREAD == 0.02


def test_v6_two_speed_and_vol_scale():
    step = bs.two_speed_step
    assert step("normal", 100, 90, 95, 94) == "normal"           # above the slow line: normal
    assert step("normal", 80, 90, 95, 94) == "off"               # breaks the slow line: off
    assert step("off", 96, 100, 95, 96) == "off"                 # above a FALLING fast line: stays off
    assert step("off", 96, 100, 95, 94) == "recovery"            # above a rising fast line: re-enter
    assert step("recovery", 94, 100, 95, 94) == "off"            # loses the fast line: out again
    assert step("recovery", 101, 100, 95, 94) == "normal"        # regains the slow line
    assert step("off", 96, None, 95, 94) == "off"                # a hole keeps the state
    assert bs.vol_scale(None, 0.20, 1.5) == 1.0 and bs.vol_scale(0.15, None, 1.5) == 1.0
    assert bs.vol_scale(0.15, 0.30, 1.5) == 0.5                  # half exposure at twice the target vol
    assert bs.vol_scale(0.15, 0.05, 1.5) == 1.5                  # capped at max_lev
    assert bs.vol_scale(0.15, 1.00, 1.5) == bs.VOL_SCALE_MIN     # floored
    # on the synthetic collapse, two_speed re-enters before the slow line is regained
    d0 = dt.date(2010, 1, 4)
    cal = []
    d = d0
    while len(cal) < 1000:
        if d.weekday() < 5:
            cal.append(d)
        d += dt.timedelta(days=1)
    spy = [100 * (1.001 ** i) for i in range(600)]
    spy += [spy[-1] * (1 - 0.006 * k) for k in range(1, 61)]                       # -36% in 60 bars
    spy += [spy[-1] * (1.003 ** k) for k in range(1, 1000 - len(spy) + 1)]         # V recovery
    px = {"SPY": _series(cal, spy), "TLT": _series(cal, [100.0] * 1000), "GLD": _series(cal, [100.0] * 1000)}
    base = dict(bs.DEFAULTS)
    base.update(start=cal[300], end=cal[-1], top_n=1, a_weight=1.0, b_weight=0.0, b_slots=0,
                pool="index_eq", abs_mom=False, sma_filter=False, rebalance="monthly", force_liquidate=True)
    slow = bs.simulate(px, [], {}, dict(base, regime="weekly200"))
    fast = bs.simulate(px, [], {}, dict(base, regime="two_speed"))
    re_slow = min(t["entry_date"] for t in slow["trades"] if t["entry_date"] > cal[660])
    re_fast = min(t["entry_date"] for t in fast["trades"] if t["entry_date"] > cal[660])
    assert re_fast < re_slow
    assert fast["stats"]["final_equity"] > slow["stats"]["final_equity"]
    # vol targeting without leverage never exceeds the plain slot; with max_lev it rides calls
    vt = bs.simulate(px, [], {}, dict(base, regime="none", vol_target=0.05, max_lev=1.0))
    assert not any(t["reason"].endswith("_call") for t in vt["trades"])
    assert any(t["reason"] == "resize" for t in vt["trades"]) or vt["stats"]["n_trades"] >= 1
    lv = bs.simulate(px, [], {}, dict(base, regime="none", vol_target=0.15, max_lev=1.5))
    assert any(t["reason"].endswith("_call") for t in lv["trades"])


def test_series_defects_guard():
    """The 'AI' splice ($2.83 Arlington → $100+ C3.ai) and TFIN's 2008→2022
    hole: a held ladder exits at the last real print; a dot on a broken
    tape is refused. Neither looks ahead."""
    d0 = dt.date(2010, 1, 4)
    cal = []
    d = d0
    while len(cal) < 900:
        if d.weekday() < 5:
            cal.append(d)
        d += dt.timedelta(days=1)
    flat = [100.0] * 900
    # XYZ: $3 for 500 bars, then the symbol is reused at $120
    xyz = [3.0] * 500 + [120.0] * 400
    defs = bs.series_defects(cal, xyz)
    assert defs == [(cal[500], "splice")]
    # a hole: bars 0-399, then nothing for a year, then bars resume
    holey_dates = cal[:400] + cal[650:]
    assert bs.series_defects(holey_dates, [5.0] * len(holey_dates)) == [(cal[650], "gap")]
    px = {"SPY": _series(cal, [100 * (1.0003 ** i) for i in range(900)]), "TLT": _series(cal, flat),
          "GLD": _series(cal, flat), "XYZ": _series(cal, xyz)}
    p = dict(bs.DEFAULTS)
    p.update(start=cal[300], end=cal[-1], top_n=1, a_weight=0.5, b_weight=0.5, b_slots=1, b_hold=400,
             pool="index_eq", abs_mom=False, sma_filter=False, regime="none")
    # dot before the splice: bought at $3, must exit at $3 on the splice bar, never at $120
    res = bs.simulate(px, [("XYZ", cal[450], 3.0)], {}, p)
    t = [t for t in res["trades"] if t["ticker"] == "XYZ"]
    assert len(t) == 1 and t[0]["reason"] == "series_splice" and t[0]["exit_date"] == cal[500]
    assert abs(t[0]["exit_px"] - 3.0) < 1e-9 and abs(t[0]["pnl"]) < 200        # costs only, no phantom 40x
    assert res["stats"]["defect_exits"] == 1
    # dot AFTER the splice (inside two years of it): refused
    res2 = bs.simulate(px, [("XYZ", cal[520], 120.0)], {}, p)
    assert not any(t["ticker"] == "XYZ" for t in res2["trades"])
    assert res2["stats"]["dots_refused_defect"] == 1
    # every run stamps the hygiene version it ran under; the boot grid re-runs older rows
    assert bs.DEFAULTS["defect_guard"] == bs.DEFECT_GUARD_VERSION
    assert "_pre_guard" in inspect.getsource(bs.run_build_grid)
    # leveraged / inverse ETPs are refused by name; companies with unlucky names are not
    for name in ("Direxion Daily Junior Gold Miners Index Bull 2X ETF", "ProShares - UltraShort Bloomberg Crude Oil",
                 "ProShares - UltraPro Short QQQ", "MicroSectors U.S. Big Banks 3 Leveraged ETN",
                 "Tradr 2X Short TSLA Daily ETF", "ProShares - Ultra QQQ", "Direxion Daily S&P 500 Bear 3X ETF"):
        assert bs.is_leveraged_etp(name), name
    for name in ("Build-A-Bear Workshop, Inc.", "UiPath Inc.", "Ultragenyx Pharmaceutical Inc.",
                 "Ultra Clean Holdings, Inc.", "Ultrapar Participações S.A.", "FuelCell Energy, Inc.", None, ""):
        assert not bs.is_leveraged_etp(name), name


def test_v8_stock_sleeve():
    """pool='stocks': names come from the monthly ranking, the absolute filter
    and the tape-defect refusal apply, a thin month pads one slot with
    bonds, and a held name whose tape breaks leaves at the last print."""
    d0 = dt.date(2010, 1, 4)
    cal = []
    d = d0
    while len(cal) < 900:
        if d.weekday() < 5:
            cal.append(d)
        d += dt.timedelta(days=1)
    flat = [100.0] * 900
    up = [10 * (1.001 ** i) for i in range(900)]
    down = [10 * (0.999 ** i) for i in range(900)]
    spliced = [5.0] * 700 + [80.0] * 200                     # symbol reused at bar 700
    px = {"SPY": _series(cal, [100 * (1.0003 ** i) for i in range(900)]), "TLT": _series(cal, up),
          "GLD": _series(cal, flat), "AAA": _series(cal, up), "BBB": _series(cal, down), "CCC": _series(cal, spliced)}
    me = bs.month_ends(cal)
    cands = {cal[i]: [("AAA", 0.30), ("CCC", 0.20), ("BBB", -0.20)] for i in me}
    p = dict(bs.DEFAULTS)
    p.update(start=cal[300], end=cal[-1], top_n=3, a_weight=1.0, b_weight=0.0, b_slots=0, pool="stocks",
             abs_mom=True, sma_filter=False, rebalance="monthly", regime="none", defensive="bonds")
    res = bs.simulate(px, [], {}, p, stock_cands=cands)
    held = {t["ticker"] for t in res["trades"]}
    assert "AAA" in held and "BBB" not in held                    # absolute filter refused the faller
    assert "TLT" in held                                          # thin month: a slot went to bonds
    ccc = [t for t in res["trades"] if t["ticker"] == "CCC"]
    assert ccc and ccc[0]["reason"] == "series_splice" and ccc[0]["exit_date"] == cal[700]
    assert abs(ccc[0]["exit_px"] - 5.0) < 1e-9                     # last real print, never $80
    assert res["stats"]["defect_exits"] >= 1
    # after the splice CCC is refused for two years even though it still ranks
    assert not any(t["ticker"] == "CCC" and t["entry_date"] > cal[700] for t in res["trades"])
    assert res["stats"]["final_equity"] > 100_000


def test_v9_fundamentals_gates():
    """Point-in-time gates: a hole never passes, stale filings never pass,
    quality wants positive TTM earnings with cash flow above them, growth
    wants revenue up more than growth_min on eight quarters."""
    d = dt.date(2019, 6, 28)
    good = dict(ttm_ni=100.0, ttm_ocf=150.0, ttm_rev=1000.0, ttm_rev_prev=800.0, n4=4, n8=8,
                last_report=dt.date(2019, 5, 1))
    ok = bs.fund_gate_ok
    assert ok(None, (), d) and ok(good, (), d)                                  # no gate: everything passes
    assert not ok(None, ("quality",), d)                                        # a hole never passes a gate
    assert ok(good, ("quality",), d) and ok(good, ("growth",), d) and ok(good, ("quality", "growth"), d)
    assert not ok(dict(good, last_report=dt.date(2018, 12, 1)), ("quality",), d)  # stale filing
    assert not ok(dict(good, ttm_ni=-1.0), ("quality",), d)                     # loses money
    assert not ok(dict(good, ttm_ocf=90.0), ("quality",), d)                    # accruals above cash
    assert not ok(dict(good, n4=3), ("quality",), d)                            # missing quarter
    assert not ok(dict(good, ttm_rev=850.0), ("growth",), d)                    # +6% < 10%
    assert ok(dict(good, ttm_rev=850.0), ("growth",), d, growth_min=0.05)
    assert not ok(dict(good, n8=7), ("growth",), d)
    assert not ok(dict(good, ttm_rev_prev=0.0), ("growth",), d)
    # the seeder takes the EARLIEST filing per period so restatements cannot leak back
    src = inspect.getsource(bs._ensure_stock_fund)
    assert "report_date <= m.me_date" in src and "f.report_date ASC" in src and "DISTINCT ON (f.period_end_date)" in src
    for n in ("v9_q10", "v9_qg10", "v9_growthrank10"):
        assert bs.BUILD_VARIANTS[n]["pool"] == "stocks" and bs.BUILD_VARIANTS[n]["fund_gates"]


def test_sealed_runs_once_and_scope():
    src = inspect.getsource(bs.run_variant)
    assert "refusing to re-run" in src and 'window == "sealed"' in src
    # the freeze: exactly two sealed runs, byte-identical to their build-window definitions
    assert list(bs.SEALED_VARIANTS) == ["v7_skip21_bonds", "v8_lev3"]
    for n, ov in bs.SEALED_VARIANTS.items():
        assert ov == bs.BUILD_VARIANTS[n]
    assert bs.SEALED_VARIANTS["v7_skip21_bonds"]["mom_skip"] == 21 and bs.SEALED_VARIANTS["v7_skip21_bonds"]["defensive"] == "bonds"
    assert bs.SEALED_VARIANTS["v8_lev3"]["lev"] == 3.0
    assert 'run_variant(n, overrides, "sealed")' in inspect.getsource(bs.run_sealed_grid)
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
