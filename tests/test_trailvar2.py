"""Trail-variant extension II (2026-09-02), pinned:
  1. kalman_levels tracks the closes with a converging gain and never
     invents a level when ATR is missing.
  2. mad_series is None inside its warmup, then the MAD of the window.
  3. sim_variants: decisions only at completed 5m boundaries; kalman
     exits on a close below the filtered level after +1R; MAD trail
     ratchets off the running high; disaster is touch-based; the struct
     stop governs before +1R.
  4. Writes only trailvar2_events (by signature).
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import trailvar2_study as tv  # noqa: E402


def test_kalman_and_mad_series():
    closes = [100.0] * 5 + [101.0] * 5
    atr = [1.0] * 10
    k = tv.kalman_levels(closes, atr, 1.0)
    assert k[0] == 100.0 and 100.0 < k[-1] < 101.0      # tracks, lags
    assert all(k[i] <= k[i + 1] for i in range(4, 9))      # rises toward 101
    assert tv.kalman_levels([100.0, 101.0], [0, 0], None) == [None, None]
    m = tv.mad_series(list(range(30)), n=20)
    assert m[18] is None and m[19] == 5.0                  # warmup then MAD


def test_sim_semantics():
    t0 = dt.datetime(2026, 9, 2, 10, 0)

    def bars(specs):
        return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]
    entry, struct = 100.0, 99.0
    specs = [(100, 101.2, 99.9, 101.0)] * 5      # arms (+1R = 101)
    specs += [(104, 105.0, 103.9, 104.8)] * 5    # running high 105
    specs += [(102.2, 102.3, 101.9, 102.0)] * 5  # 5m close 102
    b = bars(specs)
    # boundaries at 1m index 4, 9, 14
    kal = {4: 90.0, 9: 90.0, 14: 103.0}          # level above the 102 close
    mad = {4: 0.5, 9: 0.5, 14: 0.5}              # 105 - 3*0.5 = 103.5 > 102
    out = tv.sim_variants(b, 0, entry, struct, kal, mad)
    assert out["kalman_5m"]["out"] == "trail" and out["kalman_5m"]["exit_px"] == 102.0
    assert out["mad_trail"]["out"] == "trail" and out["mad_trail"]["exit_px"] == 102.0
    # a mid-block dip does not decide: same tape, kal level only at 14 is low
    out2 = tv.sim_variants(b, 0, entry, struct, {4: 90.0, 9: 90.0, 14: 90.0},
                           {4: 5.0, 9: 5.0, 14: 5.0})
    assert out2["kalman_5m"]["out"] == "eod" and out2["mad_trail"]["out"] == "eod"
    # disaster touch before arming
    out3 = tv.sim_variants(bars([(100, 100.1, 98.9, 100.0)] * 5), 0, entry,
                           struct, {4: 90.0}, {4: 1.0})
    assert out3["kalman_5m"]["out"] == "disaster"
    # struct stop on a 5m close before arming
    out4 = tv.sim_variants(bars([(100, 100.4, 99.2, 98.95)] * 5), 0, entry,
                           struct, {4: 90.0}, {4: 1.0})
    assert out4["mad_trail"]["out"] == "stopped"


def test_writes_own_table_only():
    src = inspect.getsource(tv)
    assert "INSERT INTO trailvar2_events" in src
    for forbidden in ("paper_trades", "paper_specs", "INSERT INTO trailvar_events"):
        assert forbidden not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
