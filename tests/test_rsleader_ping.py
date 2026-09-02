"""The morning RS-leader/flip pings, pinned (2026-08-31).

  1. ONE DEFINITION: the ping imports rank/entry/constants from the
     graded study — no local reimplementation of the trade.
  2. The GO alert's entry math is the study's: find_go_entry on a
     qualifying bar returns the bar's close and the wick-rule stop.
  3. Read-only over the books, at-most-once by claim — by signature.
  4. The partial-bar guard exists: the currently-forming minute never
     reaches the entry check.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import rsleader_ping  # noqa: E402


def test_one_definition_imported_never_reimplemented():
    src = inspect.getsource(rsleader_ping)
    assert "from analysis.rsleader_study import" in src
    for name in ("rs_rank", "find_go_entry", "ema", "RS_MIN", "MEASURE",
                 "ENTRY_CUTOFF"):
        assert name in src
    # no local redefinition of the graded functions
    assert "def rs_rank" not in src and "def find_go_entry" not in src
    assert "def ema(" not in src


def test_go_math_is_the_studys():
    import datetime as dt

    from analysis.rsleader_study import STOP_BUFF, ema, find_go_entry
    ts0 = dt.datetime(2026, 9, 1, 9, 45)
    bars = [(ts0 + dt.timedelta(minutes=i), 100.4, 100.5, 100.3, 100.4)
            for i in range(6)]
    bars[3] = (ts0 + dt.timedelta(minutes=3), 100.3, 100.4, 99.95, 100.2)
    closes = [b[4] for b in bars]
    e8, e21 = ema(closes, 8), ema(closes, 21)
    got = find_go_entry(bars, e8, e21, 0, 6, "long")
    assert got is not None
    i, entry, stop = got
    assert entry == bars[i][4]                       # the candle's CLOSE
    assert stop == bars[i][3] * (1 - STOP_BUFF)      # under the pullback bar


def test_read_only_and_claimed():
    src = inspect.getsource(rsleader_ping)
    assert "claim_and_send" in src
    assert "INSERT INTO" not in src and "UPDATE " not in src
    assert "DELETE FROM" not in src


def test_partial_bar_guard():
    src = inspect.getsource(rsleader_ping._today_1m)
    assert "cutoff" in src and "t < cutoff" in src




def test_trade_watch_lifecycle_definitions():
    import inspect

    from alerts import rsleader_ping as rp
    src = inspect.getsource(rp.run_trade_watch)
    # ONE DEFINITION (2026-09-02, the 11:09 phantom exit ping): the
    # watcher imports the BOOK's lifecycle_state — no local copy of the
    # trail frame, so the ping can never diverge from the ledger again.
    assert "from analysis.rs_leader_book import lifecycle_state" in src
    assert "lifecycle_state(bars, i_go, entry, stop)" in src
    assert "res5" not in src and "e21_by_min" not in src
    # the three state pings exist with distinct claim kinds
    assert rp.KIND_ARM == "rsl_arm" and rp.KIND_EXIT == "rsl_exit"
    assert rp.KIND_BELL == "rsl_bell"
    for needle in ("disaster cap touched", "21-EMA trail",
                   "closed through the stop", "AT THE CLOSE"):
        assert needle in src


def test_go_message_precomputes_the_numbers():
    import inspect

    from alerts import rsleader_ping as rp
    src = inspect.getsource(rp.run_go_watch)
    assert "arm = entry + risk" in src            # the +1R switch price
    assert "0.70 * risk * 100" in src             # per-contract dollars
    assert "size_line" in src                     # sizing done FOR Eric


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
