"""The options-expression shadow + fundamentals tag, pinned (2026-08-27).

The contract:
  1. Names are never chosen here — the swing book's signals are the
     universe; this layer only expresses or explains why not.
  2. pick_contract prefers the delta closest to 0.70 when greeks exist,
     falls back to the ~0.85x-spot strike when they don't, and every
     refusal names its reason (illiquid / no_chain / no_mark).
  3. Tenor follows the class: weekly signals 55-100 DTE, daily 28-50.
  4. The module reads the books and writes ONLY options_expression —
     by signature it cannot touch paper_trades/paper_specs.
  5. The fundamentals tag renders holes as holes (reason strings), and
     its warning renderer keeps direction: Z < 1.8 says distress,
     earnings inside ~a hold says so.
  6. The tag is stamped AFTER curation — the writer's candidate
     selection (build_gamma_specs + the kept-loop) contains no
     fundamentals reference, pinned by signature.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.fundamentals_tag import flag_line  # noqa: E402
from analysis.options_expression import (DAILY_DTE, WEEKLY_DTE,  # noqa: E402
                                         dte_window, pick_contract)


def _row(strike, delta=None, oi=500, last=5.0, exp="2026-10-16", occ="X"):
    return {"strike": strike, "delta": delta, "oi": oi, "last": last,
            "exp": exp, "occ": occ, "iv": 0.4}


def test_delta_pick_prefers_070():
    rows = [_row(80, delta=0.85), _row(90, delta=0.72), _row(100, delta=0.55)]
    pick, verdict, _ = pick_contract(rows, 100.0)
    assert verdict == "ticket" and pick["strike"] == 90


def test_no_greeks_falls_back_to_085x_spot():
    rows = [_row(70), _row(85), _row(100)]
    pick, verdict, _ = pick_contract(rows, 100.0)
    assert verdict == "ticket" and pick["strike"] == 85


def test_refusals_name_their_reason():
    _, v, note = pick_contract([], 100.0)
    assert v == "no_chain" and note
    _, v, note = pick_contract([_row(85, oi=12)], 100.0)
    assert v == "illiquid" and "12" in note
    _, v, note = pick_contract([_row(85, last=None)], 100.0)
    assert v == "no_mark"


def test_tenor_follows_class():
    assert dte_window("retest_cup_handle_weekly") == WEEKLY_DTE
    assert dte_window("retest_bull_flag_daily") == DAILY_DTE


def test_expression_module_writes_only_its_own_table():
    from analysis import options_expression
    src = inspect.getsource(options_expression)
    assert "UPDATE paper_" not in src
    assert "INSERT INTO paper_" not in src
    assert "INSERT INTO options_expression" in src


def test_fundamentals_flag_line_keeps_direction_and_holes():
    assert "unavailable" in flag_line({"reason": "no financial_scores row"})
    line = flag_line({"piotroski": 7, "altman_z": 1.2, "days_to_earnings": 10})
    assert "F7" in line and "⚠distress" in line and "⚠inside-hold" in line
    calm = flag_line({"piotroski": 5, "altman_z": 4.0, "days_to_earnings": 60})
    assert "⚠" not in calm


def test_arming_stays_blind_to_fundamentals_by_signature():
    from analysis import paper_trader
    src = inspect.getsource(paper_trader.build_gamma_specs)
    assert "fundamentals" not in src
    # The swing writer references the tag only AFTER the kept-list is
    # fixed: the curation helpers know nothing of it.
    for fn in ("swing_candidates", "curate_swing"):
        f = getattr(paper_trader, fn, None)
        if f is not None:
            assert "fundamentals" not in inspect.getsource(f)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
