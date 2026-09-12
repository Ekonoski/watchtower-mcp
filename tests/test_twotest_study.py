"""The two-test entry engine (2026-09-12), pinned:
  1. Pivots are 1-bar fractals confirmed by the following bar.
  2. Structure: L1 → H → L2 with L2 ABOVE L1 and at/above the level;
     a lower second low is not L2 (the walk keeps looking); the trigger
     is the first bar after L2 is CONFIRMED whose high crosses H — a
     cross on the confirming bar itself does not count (the structure
     was not visible yet). Entry = H's high.
  3. Kills: a print through L1's low before the trigger (l1_lost); a
     completed 5m close back through the level (level_lost); no cross
     by the cutoff = no_trigger. Shorts mirror.
  4. first_obstacle picks the nearest level beyond the entry; none =
     None (a hole). sim_targets: stop-and-target in one bar = stop.
  5. One definition: resample5 / sim_stops / level_machine / atr_series
     are IMPORTED from tapeentry_study; writes only its own tables.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import twotest_study as tt  # noqa: E402


def _b(i, o, h, l, c, d=dt.date(2026, 9, 11)):
    return (dt.datetime(d.year, d.month, d.day, 9, 30) + dt.timedelta(minutes=i), o, h, l, c)


def _mk(seq):
    return [_b(i, *x) for i, x in enumerate(seq)]


def test_pivots_are_confirmed_fractals():
    bars = _mk([(10, 11, 9, 10), (10, 10.5, 8, 9), (9, 12, 8.5, 11), (11, 11.5, 10, 10.5), (10.5, 13, 10.2, 12)])
    pv = tt.pivots(bars, 0, len(bars))
    assert (1, "low", 8) in pv and (2, "high", 12) in pv
    assert all(i < len(bars) - 1 for i, _, _ in pv)          # the last bar can never be a pivot


def test_two_test_structure_and_trigger():
    level = 100.2
    # confirm 5m bar = bars 0-4 (closes 100.5 over 100.2); then 1m: L1 at bar 6
    # (100.1), H at bar 8 (100.6), a pivot low at bar 10 (100.15 — above L1 but
    # BELOW the level, so not L2), L2 at bar 12 (100.3), confirmed by bar 13,
    # cross of H at bar 14.
    seq = [(99.8, 100.3, 99.7, 100.25), (100.25, 100.4, 100.2, 100.3), (100.3, 100.6, 100.25, 100.5),
           (100.5, 100.7, 100.4, 100.6), (100.6, 100.7, 100.3, 100.5),
           (100.5, 100.55, 100.2, 100.3), (100.3, 100.35, 100.1, 100.2),   # bar 6: L1 low 100.1
           (100.2, 100.45, 100.15, 100.4), (100.4, 100.6, 100.3, 100.5),    # bar 8: H high 100.6
           (100.5, 100.55, 100.2, 100.3), (100.3, 100.35, 100.15, 100.25), # bar 10: pivot 100.15 < level → not L2
           (100.25, 100.5, 100.35, 100.45), (100.45, 100.5, 100.3, 100.4), # bar 12: L2 low 100.3
           (100.4, 100.55, 100.35, 100.5),                                   # bar 13 confirms L2
           (100.5, 100.7, 100.45, 100.65),                                   # bar 14: crosses 100.6
           (100.65, 100.9, 100.6, 100.8)]
    bars = _mk(seq)
    bars5, last1m = tt.resample5(bars)
    r = tt.two_test(bars, level, "long", 5, len(bars) - 1, bars5, last1m, 0)
    assert r["status"] == "triggered", r
    assert r["l1"] == (6, 100.1) and r["h"] == (8, 100.6) and r["l2"] == (12, 100.3)
    assert r["i_trig"] == 14 and r["entry"] == 100.6
    # a cross on the bar that CONFIRMS L2 does not count: make bar 13 print 100.7
    seq2 = list(seq)
    seq2[13] = (100.4, 100.7, 100.35, 100.5)
    seq2[14] = (100.5, 100.55, 100.45, 100.5)
    r2 = tt.two_test(_mk(seq2), level, "long", 5, len(seq2) - 1, *tt.resample5(_mk(seq2)), 0)
    # bar 13's cross is ignored; the next real cross (bar 15, high 100.9) is the trigger
    assert r2["status"] == "triggered" and r2["l2"] == (12, 100.3) and r2["i_trig"] == 15, r2


def test_kills_and_no_trigger():
    level = 100.0
    base = [(99.8, 100.2, 99.7, 100.1), (100.1, 100.4, 100.0, 100.3), (100.3, 100.6, 100.2, 100.5),
            (100.5, 100.7, 100.4, 100.6), (100.6, 100.7, 100.3, 100.5),
            (100.5, 100.55, 100.2, 100.3), (100.3, 100.35, 100.1, 100.2),
            (100.2, 100.45, 100.15, 100.4), (100.4, 100.6, 100.3, 100.5)]
    # l1_lost: after H, price prints below L1's 100.1 before any trigger
    seq = base + [(100.5, 100.5, 100.0, 100.05), (100.05, 100.2, 100.02, 100.1), (100.1, 100.3, 100.05, 100.2)]
    bars = _mk(seq)
    r = tt.two_test(bars, level, "long", 5, len(bars) - 1, *tt.resample5(bars), 0)
    assert r["status"] == "structure_failed" and r["why"] == "l1_lost"
    # level_lost: a completed 5m bar (bars 5-9) closes back under the level
    seq = base + [(100.5, 100.5, 99.8, 99.9), (99.9, 100.2, 99.85, 100.1), (100.1, 100.3, 100.05, 100.2),
                  (100.2, 100.4, 100.15, 100.3), (100.3, 100.7, 100.25, 100.65)]
    bars = _mk(seq)
    r = tt.two_test(bars, level, "long", 5, len(bars) - 1, *tt.resample5(bars), 0)
    assert r["status"] == "structure_failed" and r["why"] == "level_lost", r
    # no_trigger: structure forms, nothing crosses H by the cutoff
    seq = base + [(100.5, 100.55, 100.28, 100.4), (100.4, 100.5, 100.35, 100.45), (100.45, 100.55, 100.4, 100.5),
                  (100.5, 100.58, 100.42, 100.5), (100.5, 100.55, 100.45, 100.5)]
    bars = _mk(seq)
    r = tt.two_test(bars, level, "long", 5, len(bars) - 1, *tt.resample5(bars), 0)
    assert r["status"] == "no_trigger" and r["l2"] == (9, 100.28), r


def test_short_mirror():
    level = 100.0
    seq = [(100.2, 100.3, 99.8, 99.9), (99.9, 100.0, 99.6, 99.7), (99.7, 99.8, 99.4, 99.5),
           (99.5, 99.6, 99.3, 99.4), (99.4, 99.7, 99.3, 99.5),
           (99.5, 99.8, 99.45, 99.7), (99.7, 99.9, 99.65, 99.8),      # bar 6: H1 high 99.9
           (99.8, 99.85, 99.55, 99.6), (99.6, 99.7, 99.4, 99.5),        # bar 8: L low 99.4
           (99.5, 99.8, 99.45, 99.7), (99.7, 99.85, 99.6, 99.7),        # bar 10: H2 high 99.85 < 99.9, below level
           (99.7, 99.75, 99.55, 99.6),                                   # bar 11 confirms H2
           (99.6, 99.65, 99.3, 99.35),                                   # bar 12: crosses below 99.4
           (99.35, 99.4, 99.1, 99.2)]
    bars = _mk(seq)
    r = tt.two_test(bars, level, "short", 5, len(bars) - 1, *tt.resample5(bars), 0)
    assert r["status"] == "triggered" and r["entry"] == 99.4 and r["i_trig"] == 12


def test_obstacle_and_targets():
    assert tt.first_obstacle("long", 100.6, [100.0, 101.35, 102.0, None]) == 101.35
    assert tt.first_obstacle("long", 100.6, [100.0, None]) is None
    assert tt.first_obstacle("short", 99.4, [98.65, 100.0, 97.0]) == 98.65
    bars = _mk([(100.6, 100.7, 100.5, 100.65), (100.65, 101.4, 100.2, 100.3)])   # bar 1 hits target AND stop
    r = tt.sim_targets(bars, 1, 100.6, "long", 100.25, 101.35)
    assert r["tp1"]["out"] == "stopped" and r["bracket2r"]["out"] == "stopped"
    bars = _mk([(100.6, 100.7, 100.5, 100.65), (100.65, 101.4, 100.4, 101.3)])
    r = tt.sim_targets(bars, 1, 100.6, "long", 100.25, 101.35)
    assert r["tp1"]["out"] == "target" and abs(r["tp1"]["r"] - 0.75 / 0.35) < 1e-2
    assert r["bracket2r"]["out"] == "target" and r["bracket2r"]["r"] == 2.0
    assert tt.sim_targets(bars, 1, 100.6, "long", 100.25, None)["tp1"] is None


def test_orb_clock_is_a_valid_time():
    # 2026-09-12 first pass: dt.time(9, 30 + 30) raised "minute must be in 0..59"
    # and every name failed silently three times. The range ends at 10:00.
    assert tt.ORB_END == dt.time(10, 0) and tt.ORB_LAST_5M == dt.time(9, 59)
    assert "dt.time(9, 30 + ORB_MIN" not in inspect.getsource(tt)


def test_one_definition_and_writes_own_tables():
    src = inspect.getsource(tt)
    assert "from analysis.tapeentry_study import" in src
    for name in ("resample5", "sim_stops", "level_machine", "atr_series"):
        assert f"def {name}(" not in src, name
    assert "INSERT INTO twotest_events" in src and "INSERT INTO twotest_days" in src
    for forbidden in ("INSERT INTO paper_", "UPDATE ", "DELETE FROM", "INSERT INTO tapeentry"):
        assert forbidden not in src, forbidden
    assert "LEAST(GREATEST(" in tt.READOUT_SQL


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
