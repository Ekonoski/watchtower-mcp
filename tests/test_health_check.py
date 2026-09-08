"""The morning health census (2026-09-08): read-only by signature, every
expected ping named with its due time, the render survives a failing
section (it prints 'unavailable', never crashes), and the 9:55 job is
registered."""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import health_check as hc  # noqa: E402


class _Cur:
    def __init__(self, fail=False):
        self.fail = fail
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, args=None):
        if self.fail:
            raise RuntimeError("relation does not exist")
    def fetchone(self):
        return (None,)
    def fetchall(self):
        return []


class _Conn:
    def __init__(self, fail=False):
        self.fail = fail
        self.rolled_back = 0
    def cursor(self):
        return _Cur(self.fail)
    def rollback(self):
        self.rolled_back += 1


def test_read_only_and_expected_pings():
    src = inspect.getsource(hc)
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert forbidden not in src, forbidden
    kinds = [k for k, _, _ in hc.EXPECTED_PINGS]
    assert kinds == ["gamma_board", "flipprox", "rsl_rank", "day_bias"]
    assert hc.EXPECTED_PINGS[-1][2] == dt.time(9, 51)          # the 📐 verdict
    assert hc.KIND_HEALTH == "health" and hc.CHANNEL == "desk"


def test_render_survives_a_broken_section():
    now = dt.datetime(2026, 9, 8, 9, 55, tzinfo=hc.ET)
    text = hc.render_health(_Conn(fail=True), now)
    assert "Morning health" in text and text.count("*unavailable*") == 4    # every section named its hole
    ok = hc.render_health(_Conn(fail=False), now)
    assert "⚠ NOT posted" in ok                                             # at 9:55 with no rows, every ping is overdue and says so
    assert "no jobs logged" in ok                                           # an empty ingestion log is a hole


def test_pings_before_due_are_not_alarms():
    early = dt.datetime(2026, 9, 8, 9, 12, tzinfo=hc.ET)
    lines = hc.pings_lines(_Conn(), early)
    assert any("gamma board: ⚠ NOT posted" in ln for ln in lines)          # 8:05 has passed
    assert any("day-bias verdict: due 09:51" in ln for ln in lines)         # 9:51 has not


def test_scheduled_and_exposed():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sched = open(os.path.join(root, "alerts", "scheduler.py")).read()
    assert 'id="health_morning"' in sched and 'hour="9", minute="55"' in sched
    server = open(os.path.join(root, "server.py")).read()
    assert "def watchtower_health(" in server and "health_report" in server


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
