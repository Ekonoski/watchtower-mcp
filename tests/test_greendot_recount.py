"""The green-dot recount (2026-09-07): one wavetrend definition with two
parameter sets, Cipher's two dots, the series-defect stamp, dialect
matching, and writes-own-tables-only by signature."""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import greendot_recount as gr  # noqa: E402


def test_one_wavetrend_two_dialects():
    import numpy as np
    import pandas as pd
    from analysis import oscillator as osc
    rng = np.random.default_rng(3)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    df = pd.DataFrame({"open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "volume": 1.0})
    lb1, lb2 = osc.wavetrend(df, *osc.WT_LAZYBEAR)
    cb1, cb2 = osc.wavetrend(df, *osc.WT_CIPHER_B)
    full = osc.compute_oscillator(df)
    assert np.allclose(full["wt1"].dropna(), lb1.dropna()) and np.allclose(full["wt2"].dropna(), lb2.dropna())
    assert not np.allclose(lb1.dropna()[-50:], cb1.dropna()[-50:])          # 9/12/3 is a different wave
    assert osc.WT_LAZYBEAR == (10, 21, 4) and osc.WT_CIPHER_B == (9, 12, 3)
    # the engine calls wavetrend, it does not carry a second copy of the math
    src = inspect.getsource(osc.compute_oscillator)
    assert "wavetrend(out)" in src and "_ema(ci" not in src


def test_find_crosses_flags():
    wt1 = [-80, -70, -65, -50, -20, 10, 30, 20, 10, None, 5]
    wt2 = [-75, -72, -70, -55, -25, 5, 25, 25, 15, 5, 0]
    #  i=1: -70 > -72 after -80 <= -75  -> cross-up, wt2=-72 below zero, wt1=-70 < -60 -> bottom buy
    #  i=7..8: 20 <= 25, 10 <= 15 -> no cross; i=10: prev has None -> skipped
    crosses = gr.find_crosses(wt1, wt2)
    assert crosses == [(1, True, True)]
    wt1b = [-30, -20, -10, 5, 15]
    wt2b = [-25, -22, -12, 8, 10]
    assert gr.find_crosses(wt1b, wt2b) == [(1, True, False), (4, False, False)]   # a shallow cross, then an above-zero one
    assert gr.BOTTOM_BUY_LEVEL == -60.0


def test_defect_flags_and_matching():
    d = dt.date(2020, 6, 30)
    defects = [(dt.date(2019, 1, 15), "splice"), (dt.date(2020, 11, 2), "gap")]
    assert gr.defect_flags(d, defects) == (True, True)
    assert gr.defect_flags(d, [(dt.date(2017, 1, 1), "gap")]) == (False, False)      # older than two years
    assert gr.defect_flags(d, [(dt.date(2021, 9, 1), "gap")]) == (False, False)      # beyond the forward year
    assert gr.defect_flags(d, []) == (False, False)
    assert gr.match_dots([10, 20, 30], [11, 19, 45]) == (2, 1, 1)
    assert gr.match_dots([10, 20], [10, 10]) == (1, 1, 1)                             # each B dot matches once
    assert gr.match_dots([], [1, 2]) == (0, 0, 2)


def test_writes_only_its_own_tables():
    src = inspect.getsource(gr)
    assert "INSERT INTO greendot_dots_cb" in src and "INSERT INTO greendot_recount_progress" in src
    # the only write to the existing record is the four recount columns
    assert "UPDATE greendot_dots SET defect_prior=%s, defect_fwd=%s, wt1_at_cross=%s, bottom_buy=%s" in src
    for forbidden in ("INSERT INTO greendot_dots\n", "INSERT INTO greendot_dots (", "DELETE FROM", "INSERT INTO paper_",
                      "UPDATE paper_", "trade_journal"):
        assert forbidden not in src, forbidden
    assert "from analysis.beat_spy import series_defects" in src           # one defect definition
    assert "from analysis.greendot_study import blocks_16d" in src         # the same fixed-anchor blocks
    assert "wavetrend(df, *params)" in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
