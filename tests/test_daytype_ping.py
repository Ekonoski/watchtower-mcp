"""The 📐 day-type line (2026-09-08), pinned: one definition (the ping
imports the study's features / prior / live_context and restates no
bucket math), small n and holes render as such, and the module cannot
write the books."""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import daytype_ping as p  # noqa: E402
from analysis import daytype_study as ds  # noqa: E402

PREV = dict(open=772.01, high=772.87, low=769.0, close=770.19, range_ratio=0.739, dir="down")
BAR = (dt.datetime(2026, 9, 8, 9, 30), 769.07, 769.7, 767.09, 767.29)
ROWS = [("post2016", 225, 55.1, 27.1, 60.0), ("pre2016", 221, 45.2, 33.0, 52.0)]


def test_read_renders_legs_priors_and_context():
    feats = ds.features([BAR], PREV, 5.237, vix_row={"vix": 14.4, "vix3m": 17.56},
                        gamma={"regime": "slippery", "flip": 770.93})
    raw = {"prev_rr": 0.739, "orb_rr": 0.498, "open_state": feats["open_state"],
           "vix_backwardated": feats["vix_backwardated"], "gamma_regime": "slippery", "flip_pct": 0.24}
    msg = p.format_read("SPY", "f945", feats, ROWS, raw)
    assert msg.startswith("**SPY**") and "ydy 0.74 ATR" in msg and "first bar 0.50 ATR" in msg
    assert "post-2016: chop 55% · trend 27%, green 60% of trends (n=225)" in msg
    assert "pre-2016: chop 45% · trend 33%" in msg
    assert "open inside" in msg and "VIX contango" in msg and "gamma slippery, flip 0.24% away" in msg
    assert "small n" not in msg
    post = p.format_post("f945", dt.time(9, 45), [msg])
    assert post.startswith("📐 **DAY TYPE** 09:45 read") and "Measurement only" in post


def test_small_n_and_holes_render_as_such():
    feats = ds.features([BAR], PREV, 5.237)
    msg = p.format_read("QQQ", "f945", feats, [("post2016", 12, 75.0, 8.3, None)], {"prev_rr": 0.67, "orb_rr": 0.67})
    assert "(n=12 ⚠ small n)" in msg and "pre-2016: no matching days" in msg
    assert "VIX unavailable" in msg and "None" not in msg
    hole = p.format_read("QQQ", "f945", {}, [], {})
    assert "unavailable" in hole and "hole" in hole
    # 10:30 names the 30m-range verdict, wick rule vocabulary
    bars = [BAR, (dt.datetime(2026, 9, 8, 9, 45), 767.3, 768.08, 766.82, 767.83),
            (dt.datetime(2026, 9, 8, 10, 0), 767.45, 767.63, 765.99, 766.9),    # wicked under the 30m low, closed inside
            (dt.datetime(2026, 9, 8, 10, 15), 766.9, 767.15, 766.12, 766.95)]
    f = ds.features(bars, PREV, 5.237)
    m = p.format_read("SPY", "f1030", f, ROWS, {"prev_rr": 0.739, "orb_rr": 0.71})
    assert "first hour 0.71 ATR" in m and "no close through the 30m range" in m


def test_one_definition_and_read_only_by_signature():
    src = inspect.getsource(p)
    assert "from analysis.daytype_study import features, live_context, prior" in src
    for forbidden in ("INSERT INTO paper_", "UPDATE paper_", "trade_journal", "DELETE", "def _bucket", '"a<0.25"', "def label_day"):
        assert forbidden not in src, forbidden          # no restated bucket math, no book writes
    assert "claim_and_send" in src and "today.isoformat()" in src
    assert [k for _, k, _, _ in p.CHECKPOINTS] == [k for _, k in ds.CHECKPOINTS]
    assert [kind for _, _, kind, _ in p.CHECKPOINTS] == ["daytype_945", "daytype_1000", "daytype_1030"]
    # a forming bar is never read as completed: bars are cut by END time
    assert "(t + dt.timedelta(minutes=15)).time() <= tcut" in src


def test_daily_facts_hold_no_lookahead():
    # atr[d] and prevd[d] must not change when d's own bar changes
    base = [(dt.date(2026, 1, 1) + dt.timedelta(days=i), 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(25)]
    d = base[-1][0]
    atr1, prev1 = ds.daily_facts(base)
    wild = base[:-1] + [(d, 100, 140, 60, 130)]
    atr2, prev2 = ds.daily_facts(wild)
    assert atr1[d] == atr2[d] and prev1[d] == prev2[d]
    # yesterday's range is measured against the ATR through yesterday's close — the one ATR the day has
    assert abs(prev1[d]["range_ratio"] - (base[-2][2] - base[-2][3]) / atr1[d]) < 1e-9
    assert ds.COMPLETE_MARKER == "daytype_v2"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
