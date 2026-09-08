"""The 📐 day-type line (2026-09-08), pinned: one definition (the ping
imports the study's features / prior / live_context and restates no
bucket math), ONE line per index with a verdict word (Eric, same
evening: "why add post and pre 2016? how should that help me?" — the
era check runs as a flag, never as a second row to compare by eye),
small n and disagreement never render as a confident word, holes render
as holes, and the module cannot write the books."""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import daytype_ping as p  # noqa: E402
from analysis import daytype_study as ds  # noqa: E402

PREV = dict(open=772.01, high=772.87, low=769.0, close=770.19, range_ratio=0.739, dir="down")
BAR = (dt.datetime(2026, 9, 8, 9, 30), 769.07, 769.7, 767.09, 767.29)
AGREE = [("post2016", 603, 70.8, 16.1, 55.0), ("pre2016", 540, 68.0, 19.4, 52.0)]
MIDDLE = [("post2016", 225, 55.1, 27.1, 60.0), ("pre2016", 221, 45.2, 33.0, 52.0)]


def test_verdict_pools_eras_and_withholds_on_disagreement_or_small_n():
    word, n, chop, trend, green, flag = p.verdict(AGREE)
    assert word == "RANGE LIKELY" and n == 1143 and abs(chop - 69.5) < 0.1 and flag == "eras agree"
    word, n, chop, trend, green, flag = p.verdict([("post2016", 67, 17.9, 59.7, 70.0), ("pre2016", 50, 22.0, 58.0, 65.0)])
    assert word == "TRAVEL LIKELY" and flag == "eras agree"
    # the middle of the grid is UNDECIDED even when the eras agree
    assert p.verdict([("post2016", 300, 52.0, 28.0, 55.0), ("pre2016", 300, 50.0, 30.0, 55.0)])[0] == "UNDECIDED"
    # disagreement downgrades a would-be RANGE verdict
    word, n, chop, trend, green, flag = p.verdict([("post2016", 300, 75.0, 10.0, 55.0), ("pre2016", 300, 58.0, 20.0, 55.0)])
    assert word == "UNDECIDED" and flag.startswith("⚠ eras disagree (75 / 58")
    # small n never speaks confidently
    word, n, chop, trend, green, flag = p.verdict([("post2016", 12, 92.0, 0.0, None)])
    assert word == "UNDECIDED" and flag == "⚠ small n" and green is None
    assert p.verdict([])[0] == "NO READ"


def test_one_line_per_index():
    feats = ds.features([BAR], PREV, 5.237, vix_row={"vix": 14.4, "vix3m": 17.56},
                        gamma={"regime": "slippery", "flip": 770.93})
    raw = {"prev_rr": 0.739, "orb_rr": 0.498, "open_state": feats["open_state"],
           "vix_backwardated": feats["vix_backwardated"], "gamma_regime": "slippery", "flip_pct": 0.24}
    msg = p.format_read("SPY", "f945", feats, AGREE, raw)
    assert "\n" not in msg                                            # one line
    assert msg.startswith("📐 **DAY TYPE** 9:45 · **SPY** **RANGE LIKELY**")
    assert "chop 69% · trend 18% (n=1,143, eras agree)" in msg      # pooled by n, not averaged
    assert "ydy 0.74 ATR · first bar 0.50 ATR" in msg
    assert "open inside" in msg and "VIX contango" in msg and "flip 0.24% away ⚠ hugging" in msg
    assert "post-2016" not in msg and "pre-2016" not in msg          # the eras are a flag, not rows
    mid = p.format_read("SPY", "f945", feats, MIDDLE, {**raw, "flip_pct": 0.43})
    assert "**UNDECIDED**" in mid and "hugging" not in mid
    post = p.format_post("f945", dt.time(9, 45), [msg, mid])
    assert post.count("📐") == 2 and "Measurement only" in post
    assert "Measurement only" not in p.format_post("f1030", dt.time(10, 30), [msg])   # footnote once a day


def test_holes_and_the_1030_color():
    hole = p.format_read("QQQ", "f945", {}, [], {})
    assert "unavailable" in hole and "hole" in hole and "None" not in hole
    bars = [BAR, (dt.datetime(2026, 9, 8, 9, 45), 767.3, 768.08, 766.82, 767.83),
            (dt.datetime(2026, 9, 8, 10, 0), 767.45, 769.9, 767.2, 769.8),      # CLOSED above the 30m high
            (dt.datetime(2026, 9, 8, 10, 15), 769.8, 770.4, 769.5, 770.2)]
    f = ds.features(bars, PREV, 5.237)
    travel = [("post2016", 157, 22.7, 57.6, 73.7), ("pre2016", 64, 26.6, 56.3, 83.3)]
    m = p.format_read("SPY", "f1030", f, travel, {"prev_rr": 0.739, "orb_rr": 0.85})
    assert "**TRAVEL LIKELY, green favored**" in m
    assert "first hour 0.85 ATR" in m and "closed up through the 30m range (green 76% of trends)" in m   # pooled 73.7/157 + 83.3/64
    # no break → no color word, and the wick rule: a wick over the 30m high is not a break
    wick = bars[:2] + [(dt.datetime(2026, 9, 8, 10, 0), 767.45, 769.9, 767.2, 768.6),
                       (dt.datetime(2026, 9, 8, 10, 15), 768.6, 769.0, 768.1, 768.4)]
    w = p.format_read("SPY", "f1030", ds.features(wick, PREV, 5.237), travel, {"prev_rr": 0.739, "orb_rr": 0.5})
    assert "no close through the 30m range" in w and "favored" not in w


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
    assert p.HUG_PCT == 0.3 and p.SMALL_N == 40


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
