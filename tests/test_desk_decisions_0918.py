"""Eric's 2026-09-18 rulings, pinned.

  "I want to flatten swing v1"  — the 16:20 settle flattens every open
      v1 position at the recorded final bar's CLOSE, reason 'manual', on
      and after 2026-09-21; the final bar's own stop/target still decides
      first; v2 and earlier dates are untouched; pure by signature.
  "Retire wall fades"           — the MORNING gamma book refuses the
      wall_fade family by name with the reason in its skips; flip-holds
      still arm; the live-board book (gamma_iday) keeps its wall fades.
  "Do number 3"                 — day_bias is a measurement line on the
      📒 scoreboard, printed every day, never among the judged books.

Standalone:  python3 tests/test_desk_decisions_0918.py
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import desk_events  # noqa: E402
from analysis import paper_trader as pt  # noqa: E402

UTC = dt.timezone.utc
FINAL = (dt.datetime(2026, 9, 21, 19, 45, tzinfo=UTC), 20.10, 20.35, 20.50, 20.02)


def test_flatten_is_v1_only_on_and_after_the_date():
    assert pt.SWING_V1_FLATTEN_DATE == dt.date(2026, 9, 21)
    assert pt.swing_v1_flatten_decision("swing", dt.date(2026, 9, 21), FINAL) == (20.35, "manual")
    assert pt.swing_v1_flatten_decision("swing", dt.date(2026, 9, 22), FINAL) == (20.35, "manual")
    assert pt.swing_v1_flatten_decision("swing", dt.date(2026, 9, 18), FINAL) == (None, None)
    assert pt.swing_v1_flatten_decision("swing_v2", dt.date(2026, 9, 21), FINAL) == (None, None)
    assert "conn" not in inspect.signature(pt.swing_v1_flatten_decision).parameters


def test_settle_lets_the_bar_decide_before_it_flattens():
    # A final bar closing through the stop books as a stop, not a flatten;
    # the flatten only takes what the rule left undecided.
    px, why = pt.swing_settle_decision("long", 20.20, 25.0, FINAL)
    assert (px, why) == (None, None)
    stopped = (FINAL[0], 20.30, 20.15, 20.40, 20.10)
    px, why = pt.swing_settle_decision("long", 20.20, 25.0, stopped)
    assert (px, why) == (20.15, "stop")
    src = inspect.getsource(pt.run_swing_close_settle)
    assert src.index("swing_settle_decision(") < src.index("swing_v1_flatten_decision(")
    assert "SWING_V1_FLATTEN_NOTE" in src and "notes" in src
    assert "manual" in pt.SWING_V1_FLATTEN_NOTE or "flattened" in pt.SWING_V1_FLATTEN_NOTE
    # 'manual' is legal for v1 in the audit
    from analysis.ledger_audit import LEGAL_EXITS
    assert "manual" in LEGAL_EXITS["swing"]


DAY = dt.date(2026, 9, 21)
BOARD = [("SPY", 768.96, 775.0, 750.0, 765.46, 2.415, "pinning")]


def test_morning_book_refuses_wall_fades_by_name():
    specs, skips = pt.build_gamma_specs(DAY, BOARD, "armed")           # book="gamma"
    assert {s[4] for s in specs} == {"flip_hold_765.5"}
    assert skips and skips[0][0] == "SPY" and "wall_fade_775 refused" in skips[0][1]
    assert "RETIRED 2026-09-18" in skips[0][1]
    # the shadow path (status 'shadow', morning book) inherits the refusal
    sh, _ = pt.build_gamma_specs(DAY, BOARD, "shadow")
    assert {s[4] for s in sh} == {"flip_hold_765.5"}


def test_live_board_book_keeps_its_wall_fades():
    specs, skips = pt.build_gamma_specs(DAY, BOARD, "armed", book="gamma_iday")
    assert {s[4] for s in specs} == {"wall_fade_775", "flip_hold_765.5"}
    assert skips == []
    assert set(pt.RETIRED_GAMMA_FAMILIES) == {("gamma", "wall_fade")}


def test_retirement_is_dated_so_history_replays_under_its_own_rule():
    # The replay harness and the binary shadow re-grade 2026-08-12 boards
    # with the rule that was live THAT day: wall fades still arm there.
    since, _why = pt.RETIRED_GAMMA_FAMILIES[("gamma", "wall_fade")]
    assert since == dt.date(2026, 9, 21)
    old, skips = pt.build_gamma_specs(dt.date(2026, 8, 12), BOARD, "armed")
    assert {s[4] for s in old} == {"wall_fade_775", "flip_hold_765.5"} and skips == []
    eve, _ = pt.build_gamma_specs(dt.date(2026, 9, 18), BOARD, "armed")
    assert any(s[4] == "wall_fade_775" for s in eve)


def test_scoreboard_prints_day_bias_as_measurement():
    today = dt.date(2026, 9, 21)
    rows = [("swing", 33, 5, 28, -24.5), ("day_bias", 0, 0, 0, None),
            ("rs_leader_v2", 3, 0, 3, -3.2)]
    out = desk_events.format_scoreboard(rows, [], today,
                                        measurement=[("day_bias", 19, 0)])
    assert "day_bias: 0 resolved" not in out
    assert "__day_bias__: 19 sessions · 0 fills — measurement only since 2026-09-18" in out
    assert out.index("swing: 33 resolved") < out.index("__day_bias__")
    assert "day_bias" in desk_events.MEASUREMENT_BOOKS
    src = inspect.getsource(desk_events.run_books_scoreboard)
    assert "MEASUREMENT_BOOKS" in src and "measurement=meas" in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
