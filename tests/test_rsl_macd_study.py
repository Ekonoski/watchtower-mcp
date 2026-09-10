"""The MACD-extension leg of the RS-leader GO (2026-09-09), pinned:
  1. macd() is EMA12 − EMA26 with a 9-EMA signal, seeded exactly as the
     graded study's ema seeds; fewer than 35 closes is None (a hole).
  2. block_closes honours the completed-block rule: a 5m/15m block counts
     only when a later block exists or its final minute has printed —
     a GO at 9:49 sees the 9:45–9:49 block, a GO at 9:50 does not.
  3. n_above / extended are None on any warmup (unknown is never False).
  4. One definition: lifecycle_state, outcomes_at and ema are imported,
     never restated; the module writes only rsl_macd_events.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import rsl_macd_study as ms  # noqa: E402
from analysis.rsleader_study import ema  # noqa: E402


def _bars(n, start=dt.datetime(2026, 9, 9, 9, 30), px=100.0, step=0.1):
    out = []
    for i in range(n):
        p = px + step * i
        out.append((start + dt.timedelta(minutes=i), p, p + 0.05, p - 0.05, p))
    return out


def test_macd_matches_the_studys_ema_and_holes_on_warmup():
    closes = [100 + (i % 7) * 0.3 + i * 0.02 for i in range(60)]
    line = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    sig = ema(line, 9)
    got = ms.macd(closes)
    assert got == (round(line[-1], 5), round(sig[-1], 5), round(line[-1] - sig[-1], 5))
    assert ms.macd(closes[:34]) is None and ms.macd(closes[:35]) is not None


def test_block_closes_completed_rule():
    b = _bars(20)                                   # 9:30 .. 9:49
    c5 = ms.block_closes(b, 5)
    assert len(c5) == 4 and c5[-1] == b[19][4]      # 9:45–9:49 complete at 9:49
    c5 = ms.block_closes(_bars(21), 5)              # .. 9:50: that block is forming
    assert len(c5) == 4
    c15 = ms.block_closes(b, 15)
    assert len(c15) == 1 and c15[0] == b[14][4]     # 9:30–9:44 only
    # a day boundary proves the prior day's trailing block complete
    day1 = _bars(390)                                # full session 9:30 .. 15:59
    day2 = _bars(3, start=dt.datetime(2026, 9, 10, 9, 30), px=140.0)
    c15 = ms.block_closes(day1 + day2, 15)
    assert len(c15) == 26 and c15[-1] == day1[-1][4]


def test_legs_none_on_warmup_and_counts_lines_above_zero():
    series = _bars(40)                               # 40 1m closes: 1m ok, 5m/15m warmup
    legs = ms.legs_at(series, 39)
    assert legs["1m"] is not None and legs["5m"] is None and legs["15m"] is None
    assert legs["n_above"] is None and legs["extended"] is None
    # rising tape across 3 sessions: every line above zero → extended
    series = []
    for k in range(3):
        series += _bars(390, start=dt.datetime(2026, 9, 7 + k, 9, 30), px=100 + 39 * k, step=0.1)
    legs = ms.legs_at(series, len(series) - 1)
    assert legs["n_above"] == 3 and legs["extended"] is True
    # falling tape → zero above
    series = []
    for k in range(3):
        series += _bars(390, start=dt.datetime(2026, 9, 7 + k, 9, 30), px=300 - 39 * k, step=-0.1)
    legs = ms.legs_at(series, len(series) - 1)
    assert legs["n_above"] == 0 and legs["extended"] is False


def test_one_definition_and_writes_own_table():
    src = inspect.getsource(ms)
    assert "from analysis.rs_leader_book import lifecycle_state" in src
    assert "from analysis.rsl_confirm_study import outcomes_at" in src
    assert "from analysis.rsleader_study import ema" in src
    for fn in ("def ema", "def lifecycle_state", "def outcomes_at", "def _res5"):
        assert fn not in src, fn
    assert "INSERT INTO rsl_macd_events" in src
    for forbidden in ("INSERT INTO paper_", "UPDATE ", "DELETE FROM", "INSERT INTO rs_leader_events"):
        assert forbidden not in src, forbidden
    assert "entry_kind='go_pullback'" in src
    assert "LEAST(GREATEST(" in ms.READOUT_SQL and "FILTER (WHERE outcomes->>'r_close' IS NOT NULL)" in ms.READOUT_SQL


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
