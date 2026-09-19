"""fills_audit parity + per-book gap report, pinned (Wave 1).

An open trade is complete with its entry row. A closed trade is
complete only with entry AND exit. A missing exit on an open trade
is not a hole — the exit has not happened.

gap_report counts BY BOOK. Pooling books into one gap score is
forbidden: a quiet gamma day must not hide a swing hole.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.fills_audit import (gap_report, missing_events,  # noqa: E402
                                  record_entry, record_exit)
from analysis.fills_audit_backfill import infer_payloads  # noqa: E402


class _Cur:
    def __init__(self):
        self.log = []

    def execute(self, sql, params=None):
        self.log.append(params)

    def fetchone(self):
        return None


def _events(cur):
    return [p[3] for p in cur.log]


def test_fills_audit_parity_open_trade_has_entry_row():
    cur = _Cur()
    record_entry(cur, 11, "swing_v2", "AAPL", 10.05,
                 fill_kind="confirm", expected_px=10.00)
    events = _events(cur)
    assert events == ["entry"]
    assert missing_events(None, events) == []
    report = gap_report(
        [{"id": 11, "book": "swing_v2", "exited_at": None}],
        [{"paper_trade_id": 11, "event": "entry"}],
    )
    assert report["swing_v2"]["n_open"] == 1
    assert report["swing_v2"]["complete"] == 1
    assert report["swing_v2"]["missing_entry"] == 0
    assert report["swing_v2"]["missing_exit"] == 0
    # no entry row at all is a hole, even on an open trade
    assert missing_events(None, []) == ["entry"]


def test_fills_audit_parity_closed_trade_has_entry_and_exit():
    cur = _Cur()
    record_entry(cur, 12, "two_test", "META", 100.6,
                 fill_kind="cross", expected_px=100.6)
    record_exit(cur, 12, "two_test", "META", 100.0,
                expected_px=100.15,
                evidence={"exit_reason": "stop"})
    events = _events(cur)
    closed_at = dt.datetime(2026, 9, 18, 10, 4)
    assert events == ["entry", "exit"]
    assert missing_events(closed_at, events) == []
    assert missing_events(closed_at, ["entry"]) == ["exit"]
    report = gap_report(
        [{"id": 12, "book": "two_test", "exited_at": closed_at}],
        [{"paper_trade_id": 12, "event": e} for e in events],
    )
    assert report["two_test"]["n_closed"] == 1
    assert report["two_test"]["complete"] == 1
    assert report["two_test"]["missing_exit"] == 0


def test_fills_audit_gap_report_counts_by_book():
    ts = dt.datetime(2026, 9, 18, 16, 0)
    trades = [
        {"id": 1, "book": "gamma", "exited_at": ts},
        {"id": 2, "book": "gamma", "exited_at": ts},
        {"id": 3, "book": "swing", "exited_at": ts},
        {"id": 4, "book": "swing", "exited_at": None},
        {"id": 5, "book": "day_bias", "exited_at": None},
    ]
    audits = [
        {"paper_trade_id": 1, "event": "entry"},
        {"paper_trade_id": 1, "event": "exit"},
        {"paper_trade_id": 2, "event": "entry"},     # closed, missing exit
        {"paper_trade_id": 4, "event": "entry"},     # open, complete
        {"paper_trade_id": 5, "event": "entry"},     # open, complete
        # trade 3 (swing, closed) has no rows
    ]
    report = gap_report(trades, audits)
    assert set(report) == {"gamma", "swing", "day_bias"}
    assert report["gamma"] == {
        "n_trades": 2, "n_open": 0, "n_closed": 2,
        "missing_entry": 0, "missing_exit": 1, "complete": 1,
    }
    assert report["swing"] == {
        "n_trades": 2, "n_open": 1, "n_closed": 1,
        "missing_entry": 1, "missing_exit": 1, "complete": 1,
    }
    assert report["day_bias"] == {
        "n_trades": 1, "n_open": 1, "n_closed": 0,
        "missing_entry": 0, "missing_exit": 0, "complete": 1,
    }
    # report-only structure: no pooled score the desk could mistake
    # for a single gap rate. gamma is missing one exit; swing is
    # missing a whole closed trade — those stay separate numbers.
    for banned in ("pooled", "overall", "score", "total"):
        assert banned not in report
    gamma_holes = (report["gamma"]["missing_entry"]
                   + report["gamma"]["missing_exit"])
    swing_holes = (report["swing"]["missing_entry"]
                   + report["swing"]["missing_exit"])
    assert gamma_holes == 1 and swing_holes == 2


def test_backfill_inferred_does_not_invent_pnl():
    row = {
        "id": 99, "book": "rs_leader_v2", "ticker": "META",
        "fill_kind": "close", "entry_px": 671.9, "entry_trigger": 671.9,
        "exited_at": dt.datetime(2026, 9, 15, 10, 6),
        "exit_px": 673.81, "exit_reason": "tp1_ratchet",
        "stop": 669.605, "target": 675.5,
    }
    payloads = infer_payloads(row)
    assert [p[3] for p in payloads] == ["entry", "exit"]
    assert [p[11] for p in payloads] == ["backfill_inferred", "backfill_inferred"]
    assert payloads[0][6] == 671.9 and payloads[1][6] == 673.81
    # tp1_ratchet is not stop/target — expected_px stays a hole, never
    # a computed R or a made-up level
    assert payloads[1][5] is None
    joined = " ".join(str(x) for p in payloads for x in p)
    assert "r_multiple" not in joined and "0.83" not in joined


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
