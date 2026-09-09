"""The day-type study (2026-09-08): the label from the close, the reads
from the checkpoint's bars only (no lookahead by construction), holes as
None, writes-own-tables by signature."""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import daytype_study as ds  # noqa: E402


def test_label_day():
    # ATR 5: a 6-point range closing at the top after opening low = green trend
    assert ds.label_day(100, 106, 100, 105.5, 5.0)[0] == "trend_green"
    assert ds.label_day(106, 106, 100, 100.5, 5.0)[0] == "trend_red"
    # a 3-point range on a 5-point ATR is chop whatever the close position
    assert ds.label_day(100, 103, 100, 103, 5.0)[0] == "chop"
    # a full range that closes in the middle is chop
    assert ds.label_day(100, 106, 100, 103, 5.0)[0] == "chop"
    # wide range, close in the upper third but not the top quarter: mixed
    lab, rr, cp = ds.label_day(100, 106, 100, 104.2, 5.0)
    assert lab == "mixed" and rr == 1.2 and cp == 0.7
    # green close position but the day opened above the close: not a green trend
    assert ds.label_day(106, 106.5, 100, 105.5, 5.0)[0] == "mixed"
    assert ds.label_day(100, 100, 100, 100, 5.0) == (None, None, None)
    assert ds.label_day(100, 105, 100, 104, None) == (None, None, None)


def _bar(h, m, o, hi, lo, c):
    return (dt.datetime(2026, 9, 8, h, m), o, hi, lo, c)


def test_features_read_only_the_checkpoint():
    prev = dict(open=770, high=772.87, low=766, close=770.19, range_ratio=1.1, dir="down")
    bars = [_bar(9, 30, 769.07, 770.5, 768.2, 770.2), _bar(9, 45, 770.2, 771.0, 769.5, 770.8),
            _bar(10, 0, 770.8, 771.4, 770.2, 770.9), _bar(10, 15, 770.9, 773.5, 770.7, 773.2)]
    f1 = ds.features(bars[:1], prev, 5.0, vix_row={"vix": 16.0, "vix3m": 18.0}, weekday="Tue")
    assert f1["open_state"] == "inside" and f1["gap_bucket"] == "-a<0.3" and f1["prev_close_pos"] == "mid"
    assert f1["prev_range_ratio"] == "e1+" and f1["prev_day_dir"] == "down"
    assert f1["orb_ratio"] == "b0.25-0.5" and f1["pos_in_orb"] == "top" and f1["last_vs_open"] == "up"
    assert "orb_break" not in f1                                 # not readable from one bar
    assert f1["vix_backwardated"] is False and f1["vix_bucket"] == "b15-20"
    assert f1["gamma_regime"] is None and f1["flip_prox"] is None   # no board: holes, not buckets
    f4 = ds.features(bars, prev, 5.0)
    assert f4["orb_break"] == "up"                               # the 10:15 bar CLOSED above the first-30-min high
    assert f4["orb_ratio"] == "e1+"
    # a wick above the 30-min high with a close back inside is not a break (wick rule)
    wick = bars[:3] + [_bar(10, 15, 771.2, 773.5, 770.0, 770.6)]
    assert ds.features(wick, prev, 5.0)["orb_break"] == "none"
    g = ds.features(bars[:1], prev, 5.0, gamma={"regime": "pinning", "flip": 770.5})
    assert g["gamma_regime"] == "pinning" and g["flip_prox"] == "a<0.3"
    assert ds.features([], prev, 5.0) == {} and ds.features(bars, None, 5.0) == {}


def test_checkpoints_and_signature():
    assert [k for _, k in ds.CHECKPOINTS] == ["f945", "f1000", "f1030"]
    src = inspect.getsource(ds)
    assert "INSERT INTO daytype_days" in src and "INSERT INTO daytype_progress" in src
    for forbidden in ("INSERT INTO paper_", "UPDATE paper_", "trade_journal", "DELETE FROM"):
        assert forbidden not in src, forbidden
    # the seeder cuts each checkpoint's bars by the bar's END time — a 9:30 bar is readable at 9:45
    assert "(b[0] + dt.timedelta(minutes=15)).time() <= tcut" in src
    assert "n >= 40" in ds.READOUT_SQL                          # small-n never renders as a cell


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")


def test_table_has_an_owner_after_the_seed():
    """2026-09-09: daytype_days froze at 9/4 — the seeder was marker-
    retired and nothing appended. run_append re-runs the one labeler
    without the marker gate; the scheduler owns it nightly and at boot."""
    src = inspect.getsource(ds.run_append)
    assert "_process_ticker(conn, tk, et)" in src
    assert "COMPLETE_MARKER" not in src            # the seed's marker never retires the owner
    assert "ON CONFLICT (ticker, trade_date) DO NOTHING" in inspect.getsource(ds._process_ticker)
    sched = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "alerts", "scheduler.py")).read()
    assert 'id="daytype_append"' in sched
    assert sched.count("from analysis.daytype_study import run_append") == 2   # cron + boot catch-up
