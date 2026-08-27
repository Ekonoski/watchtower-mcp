"""Fill forensics + entry-bar evidence, pinned (2026-08-27).

The contract born from the 770-fade / 768.655 questions:
  1. audit_level_print orders 1m evidence honestly: a pre-entry-minute
     print confirms, an entry-minute print is INCONCLUSIVE (aggregates
     cannot order sub-minute events), silence refutes.
  2. audit_close_value says which close the finer tape supports and
     shows both deltas — never a bare verdict.
  3. Every new fill carries entry_bar: the decided-on bar, the touch
     bar, and a flag when the "touch" was _touch()-tolerance-near
     rather than a literal range hit (the trade-87 lesson: code-correct
     is not the same as level-printed, and the record must show which).
  4. The audit module reads the books, never writes them — by signature.
"""
import datetime as dt
import inspect
import json
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.fill_audit import (audit_close_value,  # noqa: E402
                                 audit_level_print)
from analysis.paper_trader import _entry_bar_json  # noqa: E402

ET = ZoneInfo("America/New_York")


def _m(et, h, lo, c=None, o=None):
    return {"et": et, "open": o or lo, "high": h, "low": lo, "close": c or h}


def test_pre_entry_print_confirms():
    bars = [_m("10:25", 769.9, 769.5), _m("10:28", 770.1, 769.8),
            _m("10:30", 770.4, 769.9)]
    v, d = audit_level_print(bars, 770.0, "10:30")
    assert v == "confirmed" and "10:28" in d


def test_entry_minute_print_is_inconclusive_not_confirmed():
    bars = [_m("10:28", 769.9, 769.5), _m("10:30", 770.04, 769.6)]
    v, d = audit_level_print(bars, 770.0, "10:30")
    assert v == "inconclusive_sub_minute"
    assert "cannot order" in d


def test_silence_refutes_with_the_max_named():
    bars = [_m("10:25", 769.88, 769.4), _m("10:30", 769.95, 769.5)]
    v, d = audit_level_print(bars, 770.0, "10:30")
    assert v == "refuted" and "769.95" in d


def test_close_value_names_both_deltas():
    bars = [_m("10:13", 768.7, 768.5, c=768.66), _m("10:14", 768.7, 768.6, c=768.67)]
    v, d = audit_close_value(bars, "10:15", 768.655, 768.935)
    assert v == "confirmed"          # tape supports the recorded fill
    assert "768.655" in d and "768.935" in d and "Δ" in d
    v2, _ = audit_close_value(bars, "10:15", 768.935, 768.66)
    assert v2 == "refuted"


def test_entry_bar_records_near_touch_tolerance():
    ts = dt.datetime(2026, 8, 27, 10, 15, tzinfo=ET)
    # (ts, open, close, high, low, volume): high 769.88 never reaches 770,
    # close 769.57 is within _touch's 0.1% — the trade-87 shape.
    bar = (ts, 769.70, 769.57, 769.88, 769.06, 1e6)
    payload = json.loads(_entry_bar_json(bar, [bar], 770.0))
    assert payload["near_touch_tolerance"] is True
    assert payload["touch"]["close"] == 769.57
    assert payload["decided_on"]["high"] == 769.88
    # A literal range hit is NOT flagged as tolerance.
    bar2 = (ts, 769.7, 769.9, 770.1, 769.5, 1e6)
    payload2 = json.loads(_entry_bar_json(bar2, [bar2], 770.0))
    assert payload2["near_touch_tolerance"] is False
    # No touch at all: null, never invented.
    bar3 = (ts, 768.0, 768.2, 768.5, 767.9, 1e6)
    payload3 = json.loads(_entry_bar_json(bar3, [bar3], 770.0))
    assert payload3["touch"] is None


def test_audit_module_reads_books_never_writes_them():
    from analysis import fill_audit
    src = inspect.getsource(fill_audit)
    assert "UPDATE paper_" not in src
    assert "INSERT INTO paper_" not in src
    assert "DELETE FROM paper_" not in src
    assert "INSERT INTO fill_audit" in src   # its own table only


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
