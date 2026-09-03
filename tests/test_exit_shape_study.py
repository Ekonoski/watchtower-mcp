"""The day trader's exits, pinned (2026-09-03):
  1. sim_exit: TP1 fills at the touch; half-off leaves the runner at
     breakeven (close flavor ignores a wick, touch flavor does not); the
     5m-low ratchet never sits below entry; TP2 takes the rest; the
     disaster touch always wins; time stop and time exit fire at the
     bar close; the momentum-fade exits fire only once armed.
  2. shelves_asof reads nothing at/after the entry bar, and pick_targets
     honours the minimum distance.
  3. The option frame prices each leg at its own minute and loses money
     on a flat trade (decay).
  4. Scope: writes exit_shape_events only; level engine imported.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import exit_shape_study as xs  # noqa: E402


def _bars(specs, t0=dt.datetime(2026, 9, 2, 9, 45)):
    return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]


def test_engine_semantics():
    entry = 100.0
    # 5 bars flat, then a run through 101 (TP1) with a wick to 99.9 later
    # in a block that CLOSES 100.2, then a run to 102 (TP2), then bell at 101.5
    specs = [(100, 100.1, 99.9, 100.0)] * 5
    specs += [(100.5, 101.2, 100.4, 101.0)] * 5          # TP1 100.9 touched
    specs += [(100.3, 100.4, 99.9, 100.2)] * 5           # wick under entry, close above
    specs += [(101.0, 102.1, 100.9, 102.0)] * 5          # TP2 102 touched
    specs += [(101.6, 101.7, 101.4, 101.5)] * 5
    b = _bars(specs)
    full = xs.sim_exit(b, 0, entry, tp1=100.9)
    assert full["out"] == "tp1" and full["exit_px"] == 100.9
    half_close = xs.sim_exit(b, 0, entry, tp1=100.9, tp1_frac=0.5, be="close")
    assert half_close["out"] == "tp1+bell" and abs(half_close["exit_px"] - (100.9 + 101.5) / 2) < 1e-9
    half_touch = xs.sim_exit(b, 0, entry, tp1=100.9, tp1_frac=0.5, be="touch")
    assert half_touch["out"] == "tp1+stop_touch" and half_touch["exit_px"] == (100.9 + 100.0) / 2
    tp2 = xs.sim_exit(b, 0, entry, tp1=100.9, tp1_frac=0.5, be="close", tp2=102.0)
    assert tp2["out"] == "tp1+tp2" and abs(tp2["exit_px"] - (100.9 + 102.0) / 2) < 1e-9
    rat = xs.sim_exit(b, 0, entry, tp1=100.9, tp1_frac=0.5, be="close", ratchet="5mlow")
    assert rat["out"].startswith("tp1+") and rat["exit_px"] >= (100.9 + 100.0) / 2
    # disaster touch wins over everything
    knife = _bars([(100, 100.1, 99.9, 100.0)] + [(99.5, 99.6, 98.9, 99.4)] * 4)
    assert xs.sim_exit(knife, 0, entry, tp1=100.5)["out"] == "disaster"
    # time stop: flat at +30 min -> out at that bar's close
    flat = _bars([(100, 100.1, 99.9, 100.0)] * 40)
    ts = xs.sim_exit(flat, 0, entry, tstop_min=30)
    assert ts["out"] == "tstop" and ts["legs"][0][2] == flat[30][0]
    tx = xs.sim_exit(_bars(specs, dt.datetime(2026, 9, 2, 10, 50)), 0, entry, tx_time=dt.time(11, 0))
    assert tx["out"] == "tx" and tx["legs"][0][2].time() == dt.time(11, 0)
    # momentum/tail exits need the arm: no arm touch -> bell; a level set
    # and never reached -> the runner cannot be trailed out before TP1
    assert xs.sim_exit(flat, 0, entry, mom="macd", arm_bps=50)["out"] == "bell"
    fade = [(100, 100.1, 99.9, 100.0)] + [(100.4, 100.5, 100.3, 100.4)] * 10
    fade += [(100.3 - 0.02 * k, 100.35 - 0.02 * k, 100.2 - 0.02 * k, 100.25 - 0.02 * k) for k in range(15)]
    assert xs.sim_exit(_bars(fade), 0, entry, tp1=105.0, tp1_frac=0.5, be="close",
                       mom="macd")["out"] == "bell"
    assert xs.sim_exit(_bars(fade), 0, entry, tp1=105.0, tp1_frac=0.5, be="close",
                       tail="trail21")["out"] == "bell"


def test_levels_asof_and_targets():
    t_entry = dt.datetime(2026, 9, 2, 9, 47)
    daily = []
    d = dt.date(2025, 6, 2)
    px = 100.0
    for k in range(320):
        if d.weekday() < 5:
            hi = px + (3.0 if k % 40 == 20 else 0.5)      # a repeating pivot high
            daily.append({"date": d, "open": px, "high": hi, "low": px - 0.5, "close": px})
        d += dt.timedelta(days=1)
    # a "future" daily bar that must be ignored
    daily.append({"date": dt.date(2026, 9, 3), "open": 100, "high": 150, "low": 99, "close": 100})
    lv = xs.shelves_asof(daily, {}, t_entry, 100.0)
    assert lv is not None
    assert all(r["price"] < 150 for r in lv["resistance"])       # no lookahead
    tp1, tp2 = xs.pick_targets(lv["resistance"], 100.0)
    assert tp1 is None or tp1["price"] >= 100.0 * (1 + xs.MIN_TP_BPS / 1e4)
    assert xs.pick_targets([{"price": 100.2}, {"price": 100.6}, {"price": 101.0}], 100.0)[0]["price"] == 100.6
    assert xs.strike_step(590) == 5.0 and xs.strike_step(150) == 2.5


def test_option_frame_and_scope():
    t = dt.datetime(2026, 9, 2, 9, 47)
    legs = [(0.5, 100.0, t + dt.timedelta(hours=2)), (0.5, 100.0, t + dt.timedelta(hours=6))]
    of = xs.option_frame(100.0, t, legs, 0.35)
    assert set(of) == {"0.55", "0.70", "0.85"} and all(v < 0 for v in of.values())
    assert xs.option_frame(100.0, t, legs, None) is None
    src = inspect.getsource(xs)
    assert "from analysis.levels import" in src and "levels_from_points" in src
    assert "INSERT INTO exit_shape_events" in src
    for forbidden in ("paper_trades", "paper_specs", "rsl_book_bars", "def _pivots", "def _cluster"):
        assert forbidden not in src
    sched = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "alerts", "scheduler.py")).read()
    for name in ("premarket", "exit_shape", "daystate"):
        assert f'("{name}", "analysis.' in sched


def test_daystate_legs_and_premarket():
    from analysis import daystate_study as ds
    from analysis import premarket_backfill as pm
    day = {"open": 101.0, "high": 0, "low": 0, "close": 0}
    prev = {"open": 99, "high": 100.5, "low": 98.5, "close": 100.2}
    prev2 = {"open": 98, "high": 99, "low": 97, "close": 98.5}
    legs = ds.legs_for(day, prev, prev2, 500.0, 495.0, 0.01,
                       {"vix": 18.0, "vix3m": 19.0}, {"vix": 17.0},
                       {"rank_1m": 2, "rs_1w": -0.4}, "pinning")
    assert legs["open_state"] == "above_pdh" and legs["gap_bucket"] == "+b0.3-1"
    assert legs["prev_close_pos"] == "top20" and legs["prev_day_dir"] == "up"
    assert legs["spy_above_ema20"] is True and legs["vix_bucket"] == "b15-20"
    assert legs["vix_chg"] == "up" and legs["vix_backwardated"] is False
    assert legs["sector_rank"] == "top" and legs["sector_1w"] == "down"
    hole = ds.legs_for(None, None, None, None, None, None, None, None, None, None)
    assert all(v is None for v in hole.values())
    src = inspect.getsource(ds)
    assert "from analysis.day_bias import decide" in src and "INSERT INTO daystate_legs" in src
    assert "paper_trades" not in src and "paper_specs" not in src
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    ms = lambda h, m: int(dt.datetime(2026, 9, 2, h, m, tzinfo=et).timestamp() * 1000)
    rows = [(ms(7, 30), 101.0, 100.0), (ms(9, 29), 102.0, 100.5), (ms(9, 30), 110.0, 90.0)]
    r = pm.premarket_ranges(rows, et)
    assert r[dt.date(2026, 9, 2)] == (102.0, 100.0, 2)        # the 9:30 bar is not premarket
    assert "INSERT INTO premarket_range" in inspect.getsource(pm)
    assert "mag7_1m_bars" not in inspect.getsource(pm) and "liquid_1m_bars" not in inspect.getsource(pm)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
