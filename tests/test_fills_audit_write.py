"""fills_audit write contract, pinned (Wave 1).

  1. record_entry / record_exit write the ledger shape — book, ticker,
     event, got_px, expected_px, provenance — and refuse a missing
     price (a fill without a price is not a fill).
  2. If the audit INSERT fails, the paper_trades write must not
     commit. Fail closed: no silent paper without a ledger row.
  3. All four paper-trade writers (paper_trader, rs_leader_book,
     twotest_book, day_bias) call record_entry / record_exit on the
     SAME cursor as the trade INSERT / exit UPDATE, before commit.
  4. fill_audit (singular) stays the forensic Q&A table; this module
     does not write it, and that module does not write fills_audit.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from analysis.fills_audit import (INSERT_SQL, record_entry,  # noqa: E402
                                  record_exit)


def _read(rel):
    return open(os.path.join(ROOT, rel)).read()


class _Cur:
    def __init__(self, fail_audit=False):
        self.log = []
        self.fail_audit = fail_audit

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))
        if self.fail_audit and "INSERT INTO fills_audit" in sql:
            raise RuntimeError("audit insert failed")

    def fetchone(self):
        return (42,)


def test_fills_audit_record_entry_shape():
    cur = _Cur()
    record_entry(cur, 7, "gamma", "SPY", 720.10,
                 fill_kind="touch", expected_px=720.0,
                 bar={"close": 720.10},
                 evidence={"setup": "flip_hold"})
    assert len(cur.log) == 1
    sql, params = cur.log[0]
    assert "INSERT INTO fills_audit" in sql
    assert "INSERT INTO fill_audit " not in sql + " "
    assert params[0] == 7 and params[1] == "gamma" and params[2] == "SPY"
    assert params[3] == "entry" and params[4] == "touch"
    assert params[5] == 720.0 and params[6] == 720.10
    assert params[7] is False and params[8] is False
    assert "720.1" in params[9] and "flip_hold" in params[10]
    assert params[11] == "live"
    try:
        record_entry(cur, 7, "gamma", "SPY", None)
        raise AssertionError("got_px=None must fail closed")
    except ValueError as e:
        assert "got_px" in str(e)


def test_fills_audit_record_exit_shape():
    cur = _Cur()
    record_exit(cur, 7, "gamma", "SPY", 717.56,
                expected_px=717.56, phantom_stop=False,
                evidence={"exit_reason": "target"})
    sql, params = cur.log[0]
    assert params[3] == "exit" and params[6] == 717.56
    assert params[5] == 717.56 and params[8] is False
    assert "target" in params[10]
    assert "r_multiple" not in (params[10] or "")
    try:
        record_exit(cur, 7, "gamma", "SPY", 1.0, provenance="invented")
        raise AssertionError("unknown provenance must fail closed")
    except ValueError as e:
        assert "provenance" in str(e)


def test_singular_fill_audit_stays_forensic():
    assert "INSERT INTO fills_audit" not in _read("analysis/fill_audit.py")
    src = _read("analysis/fills_audit.py")
    assert "INSERT INTO paper_trades" not in src
    assert "UPDATE paper_trades" not in src
    assert INSERT_SQL.startswith("INSERT INTO fills_audit")



def test_boot_schema_commits_or_rolls_back_and_surfaces_failure():
    from analysis.fills_audit import ensure_schema
    class Conn:
        def __init__(self, fail=False):
            self.fail, self.statements, self.commits, self.rollbacks = fail, [], 0, 0
        def cursor(self):
            return self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql):
            self.statements.append(sql)
            if self.fail and "CREATE TABLE" in sql:
                raise RuntimeError("migration denied")
        def commit(self):
            self.commits += 1
        def rollback(self):
            self.rollbacks += 1
    good = Conn()
    ensure_schema(good)
    assert good.commits == 1 and good.rollbacks == 0
    assert "pg_advisory_xact_lock" in good.statements[0]
    assert good.statements[1] == _read("migrations/076_fills_audit.sql")
    bad = Conn(fail=True)
    try:
        ensure_schema(bad)
    except RuntimeError as e:
        assert str(e) == "migration denied"
    else:
        raise AssertionError("boot swallowed migration failure")
    assert bad.commits == 0 and bad.rollbacks == 1


def test_recorded_legs_are_never_rewritten_or_relabeled_live():
    from analysis.fills_audit import record_legs
    cur = _Cur()
    old = [{"frac": .5, "px": 101., "ts": "stored", "why": "tp1"}]
    record_legs(cur, 1, "two_test", "TEST", old, old, [], 99.)
    assert not cur.log   # old missing audit stays a gap until explicit backfill
    try:
        record_legs(cur, 1, "two_test", "TEST", [], old, [], 99.)
    except ValueError as e:
        assert "recorded execution legs changed" in str(e)
    else:
        raise AssertionError("must not erase old executions")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
