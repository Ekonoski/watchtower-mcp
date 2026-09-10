"""The swing MAE-vs-stop read (2026-09-10), pinned:
  1. excursion reads lows for touch-MAE, 15:45 closes for daily-MAE,
     and records whether a low touched the stop before anything else.
  2. ATR14 uses only days BEFORE the fill date (no lookahead); too few
     prior days is a hole (None), which makes the atr1 variant a hole.
  3. The post-exit path: a daily close back at/above entry is a
     shakeout; none with 20 days on record is a failure; none with
     fewer is PENDING — a hole, never a failure.
  4. replay honours the wick rule (a 15m close through the stop mid-day
     is not an exit; the 15:45 close is), fills the target on a touch,
     and returns a hole when the sequence ends unresolved.
  5. Writes only swing_mae_events, by source.
"""
import datetime as dt
import inspect
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import swing_mae_study as sm  # noqa: E402

ET = ZoneInfo("America/New_York")


def _b(day, hh, mm, o, h, l, c):
    return (dt.datetime(2026, 9, day, hh, mm, tzinfo=ET), o, h, l, c)


def test_excursion_reads_lows_closes_and_the_touch():
    entry, stop = 100.0, 95.0                       # risk 5
    bars = [_b(1, 13, 45, 100, 101, 99, 100.5),     # fill bar
            _b(1, 15, 45, 100.5, 102, 96, 97),      # low 96 = -0.8R touch-MAE; 15:45 close 97 = -0.6R daily
            _b(2, 9, 30, 97, 103, 94.9, 98),        # low 94.9 touches the stop; high 103 = +0.6R
            _b(2, 15, 45, 98, 99, 97, 98.5)]
    e = sm.excursion(bars, entry, stop, entry - stop, daily_closes=[97.0, 98.5])
    assert e["mae_touch_r"] == -1.02 and e["mae_daily_r"] == -0.6
    assert e["mfe_r"] == 0.6 and e["mfe_pre_mae_r"] == 0.4      # 102 printed before the MAE bar
    assert e["touched_stop_first"] is True and e["t_mae_days"] == 1


def test_atr_is_prior_days_only_and_a_short_history_is_a_hole():
    daily = [(dt.date(2026, 8, 1) + dt.timedelta(days=i), 10, 11, 9, 10) for i in range(20)]
    fill = dt.date(2026, 8, 16)
    a = sm.atr14(daily, fill)
    assert a is not None and abs(a - 2.0) < 1e-9
    # a day AFTER the fill with a huge range must not move it
    daily2 = daily + [(dt.date(2026, 8, 30), 10, 50, 1, 10)]
    assert sm.atr14(daily2, fill) == a
    assert sm.atr14(daily[:10], fill) is None
    assert sm.cf_stops(100.0, 95.0, None)["atr1"] is None


def test_post_path_verdicts():
    entry, target, risk = 100.0, 110.0, 5.0
    post = [(dt.date(2026, 9, d), 96, 99, 95, 98) for d in range(1, 6)]
    p = sm.post_path(post, entry, target, risk)
    assert p["reclaim_day"] is None and p["post_days"] == 5
    assert sm.verdict("stop", None, p) == "pending"           # a hole, never a failure
    full = post + [(dt.date(2026, 9, d), 96, 99, 95, 98) for d in range(6, 21)]
    assert sm.verdict("stop", None, sm.post_path(full, entry, target, risk)) == "failure"
    shk = post + [(dt.date(2026, 9, 6), 98, 101, 97, 100.5)]
    p2 = sm.post_path(shk, entry, target, risk)
    assert p2["reclaim_day"] == 6 and sm.verdict("stop", None, p2) == "shakeout"
    assert sm.verdict("target", {"touched_stop_first": True}, None) == "won_after_touch"
    assert sm.verdict(None, None, None) == "open"


def test_replay_wick_rule_target_touch_and_hole():
    entry, stop, target = 100.0, 95.0, 110.0
    d1, d2 = dt.date(2026, 9, 1), dt.date(2026, 9, 2)
    # a mid-day 15m close at 94 is NOT an exit; the 15:45 close at 96 holds
    seq = [(d1, 101, 93.5, 94, False), (d1, 97, 95.5, 96, True)]
    assert sm.replay(seq, entry, stop, target)["outcome"] == "hole"
    # the 15:45 close through the stop exits at that close
    seq2 = seq + [(d2, 97, 93, 94.5, True)]
    r = sm.replay(seq2, entry, stop, target)
    assert r == {"outcome": "stop", "exit_px": 94.5, "days": 1}
    # a wider stop survives the same tape and reaches the target on a touch
    seq3 = seq2 + [(dt.date(2026, 9, 3), 111, 99, 108, True)]
    r2 = sm.replay(seq3, entry, 90.0, target)
    assert r2["outcome"] == "target" and r2["exit_px"] == 110.0 and r2["days"] == 2
    # stop precedence when both print on one close bar
    assert sm.replay([(d1, 111, 90, 92, True)], entry, stop, target)["outcome"] == "stop"


def test_grade_trade_flags_a_phantom_stop_and_replays_official_closes():
    # HBB, 2026-08-21: the ledger stopped at 32.74 (the 15:30 bar's
    # close) while the official close printed 32.90, above the 32.75 stop.
    entered = dt.datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    exited = dt.datetime(2026, 8, 21, 15, 55, tzinfo=ET)
    row = (37, 1, "HBB", "retest_bull_flag_daily", entered, 33.9, 32.75, 45.98,
           exited, 32.74, "stop", -1.01)
    def _a(day, hh, mm, o, h, l, c):                  # August bars
        return (dt.datetime(2026, 8, day, hh, mm, tzinfo=ET), o, h, l, c)
    bars = [_a(18, 9, 45, 33.9, 34.0, 33.8, 33.9), _a(18, 15, 30, 33.9, 34.1, 33.7, 33.8),
            _a(21, 9, 30, 33.0, 33.65, 32.5, 33.0), _a(21, 15, 30, 32.9, 32.95, 32.26, 32.74)]
    daily = [(dt.date(2026, 7, 1) + dt.timedelta(days=i), 33, 34, 32, 33) for i in range(40)]
    daily = [d for d in daily if d[0] < dt.date(2026, 8, 18)]
    daily += [(dt.date(2026, 8, 18), 33.9, 34.1, 33.7, 33.8),
              (dt.date(2026, 8, 19), 33.8, 34.0, 33.5, 33.6),
              (dt.date(2026, 8, 20), 33.6, 33.9, 33.2, 33.4),
              (dt.date(2026, 8, 21), 33.0, 33.65, 32.26, 32.90),   # official close HELD
              (dt.date(2026, 8, 24), 32.9, 33.2, 32.0, 32.10)]     # then it broke
    g = sm.grade_trade(row, bars, daily, dt.date(2026, 9, 10))
    assert g["phantom_stop"] is True and abs(g["exit_day_close_r"] - (32.90 - 33.9) / 1.15) < 1e-3
    assert g["live_match"] is False
    # the replay with the live stop rides the official closes: stopped on 8/24
    assert g["cf"]["x1_0"]["outcome"] == "stop" and abs(g["cf"]["x1_0"]["r"] - (32.10 - 33.9) / 1.15) < 1e-3
    # daily MAE reads the held days' official closes: the worst is the exit day's 32.90
    assert abs(g["mae_daily_r"] - (32.90 - 33.9) / 1.15) < 1e-3 and g["n_bars"] == 4
    assert g["touched_stop_first"] is True and g["cf"]["x1_0"]["days"] == 4


def test_writes_own_table_only():
    src = inspect.getsource(sm)
    assert "INSERT INTO swing_mae_events" in src
    for forbidden in ("INSERT INTO paper_", "UPDATE paper_", "DELETE FROM", "UPDATE daily"):
        assert forbidden not in src, forbidden
    assert "LEAST(GREATEST(" in sm.READOUT_SQL


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
