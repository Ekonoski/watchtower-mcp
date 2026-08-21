"""The defended-entry shadow, pinned (2026-08-21).

Eric's signature: red volume CONTRACTING into the retest, then a green
bar (or two) closing back off the level on a volume uptick RELATIVE to
the pullback — never a spike requirement (spikes are late). What this
file pins:

  1. The defended case: quiet red pullback into the touch, green bar
     back above the trigger on rising volume → defended at that bar's
     close, premium recorded. v2 (two rising green bars) fires on the
     second bar.
  2. The knife (the CAE/HBB shape): no defense ever prints, price
     closes through the stop → knife_skipped. The wick rule holds —
     a WICK through the stop is not a knife; only a close is.
  3. The missed V-bottom (the BTGO shape): price runs off the level
     without a qualifying signature → missed, recorded as the cost of
     demanding proof.
  4. A volume spike is NOT required: defense volume merely above the
     pullback's red-bar average qualifies.
  5. Missing volume is a hole → 'unavailable', never a guess.
  6. Measurement only, by signature: the shadow module contains no
     writes to paper_trades or paper_specs.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.defense_shadow import find_defense  # noqa: E402

T0 = dt.datetime(2026, 8, 21, 10, 0)


def _bar(i, o, c, h, low, v):
    return {"ts": T0 + dt.timedelta(minutes=15 * i), "open": o, "close": c,
            "high": h, "low": low, "volume": v}


TRIG, STOP = 100.0, 97.0


def test_defended_on_relative_uptick_not_spike():
    bars = [
        _bar(0, 102.0, 101.2, 102.1, 101.0, 900),   # red, pullback starts
        _bar(1, 101.2, 100.6, 101.3, 100.4, 700),   # red, contracting
        _bar(2, 100.6, 100.1, 100.7, 99.9, 500),    # red touch bar (low<=100)
        _bar(3, 100.1, 100.4, 100.5, 100.0, 850),   # green, above trig,
                                                    # vol 850 > red avg 700
    ]
    res = find_defense(bars, TRIG, STOP, touch_idx=2)
    v1 = res["v1"]
    assert v1["status"] == "defended"
    assert v1["px"] == 100.4
    assert abs(v1["base_vol"] - (900 + 700 + 500) / 3) < 1e-9
    # 850 is no spike — merely above the pullback average. That's the
    # point: relative uptick, not fireworks.
    assert v1["defense_vol"] == 850
    assert abs(v1["premium_pct"] - 0.004) < 1e-9


def test_v2_two_rising_green_bars():
    bars = [
        _bar(0, 102.0, 101.0, 102.1, 100.9, 900),
        _bar(1, 101.0, 100.2, 101.1, 99.95, 600),   # touch
        _bar(2, 100.2, 100.35, 100.4, 100.1, 400),  # green #1, small vol
        _bar(3, 100.35, 100.6, 100.7, 100.3, 550),  # green #2, rising vol
    ]
    res = find_defense(bars, TRIG, STOP, touch_idx=1)
    assert res["v2"]["status"] == "defended"
    assert res["v2"]["px"] == 100.6


def test_knife_skipped_on_close_through_stop_wick_excused():
    bars = [
        _bar(0, 101.0, 100.3, 101.1, 100.2, 800),
        _bar(1, 100.3, 99.6, 100.4, 99.5, 900),     # red touch
        _bar(2, 99.6, 98.4, 99.7, 96.8, 1200),      # WICK to 96.8, close 98.4
        _bar(3, 98.4, 96.5, 98.5, 96.3, 1500),      # CLOSE through 97
    ]
    res = find_defense(bars, TRIG, STOP, touch_idx=1)
    # Bar 2's wick through the stop is not a knife; bar 3's close is.
    assert res["v1"]["status"] == "knife_skipped"
    assert res["v2"]["status"] == "knife_skipped"


def test_missed_v_bottom():
    bars = [
        _bar(0, 101.0, 100.2, 101.1, 100.1, 800),
        _bar(1, 100.2, 99.9, 100.3, 99.8, 700),     # touch
        _bar(2, 99.9, 100.9, 101.0, 99.9, 500),     # rips 0.9% off the level
                                                    # on FALLING volume
    ]
    res = find_defense(bars, TRIG, STOP, touch_idx=1)
    assert res["v1"]["status"] == "missed"


def test_missing_volume_is_a_hole():
    bars = [
        _bar(0, 101.0, 100.2, 101.1, 100.1, None),
        _bar(1, 100.2, 99.9, 100.3, 99.8, 700),
        _bar(2, 99.9, 100.4, 100.5, 99.9, 900),
    ]
    res = find_defense(bars, TRIG, STOP, touch_idx=1)
    assert res["v1"]["status"] == "unavailable"


def test_measurement_only_by_signature():
    from analysis import defense_shadow
    src = inspect.getsource(defense_shadow)
    for forbidden in ("UPDATE paper_trades", "INSERT INTO paper_trades",
                      "UPDATE paper_specs", "INSERT INTO paper_specs"):
        assert forbidden not in src, forbidden
    # And the study reuses the SAME detector — one definition, two reads.
    from analysis import defense_study
    ssrc = inspect.getsource(defense_study)
    assert "find_defense" in ssrc


def test_spec_bar_rows_carry_volume_and_tolerate_legacy_tuples():
    from analysis.paper_trader import _spec_bar_rows
    ts = T0
    with_vol = _spec_bar_rows("X", dt.date(2026, 8, 21),
                              [(ts, 1.0, 1.2, 1.3, 0.9, 5000.0)])
    assert with_vol == [("X", ts, 1.0, 1.3, 0.9, 1.2, 5000.0,
                         dt.date(2026, 8, 21))]
    legacy = _spec_bar_rows("X", dt.date(2026, 8, 21),
                            [(ts, 1.0, 1.2, 1.3, 0.9)])
    assert legacy[0][6] is None   # volume hole, never invented


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
