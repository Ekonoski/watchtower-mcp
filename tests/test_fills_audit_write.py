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
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.fills_audit import (INSERT_SQL, record_entry,  # noqa: E402
                                  record_exit)


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


class _Conn:
    def __init__(self, fail_audit=False):
        self.cur = _Cur(fail_audit=fail_audit)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


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


def _write_entry_same_txn(conn, **audit):
    """The writer contract: INSERT paper_trades, then record_entry,
    then commit. Any exception rolls the trade back."""
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO paper_trades (spec_id, entered_at, "
                      "entry_px) VALUES (%s, now(), %s) RETURNING id",
                      (1, 100.0))
            tid = c.fetchone()[0]
            record_entry(c, tid, **audit)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def test_fills_audit_entry_rolled_back_if_audit_fails():
    conn = _Conn(fail_audit=True)
    try:
        _write_entry_same_txn(conn, book="gamma", ticker="SPY", got_px=100.0,
                              fill_kind="touch", expected_px=100.0)
        raise AssertionError("audit failure must refuse the commit")
    except RuntimeError as e:
        assert "audit insert failed" in str(e)
    assert conn.commits == 0
    assert conn.rollbacks == 1
    sqls = [s for s, _ in conn.cur.log]
    assert any("INSERT INTO paper_trades" in s for s in sqls)
    assert any("INSERT INTO fills_audit" in s for s in sqls)
    # the happy path still commits once the audit lands
    ok = _Conn()
    _write_entry_same_txn(ok, book="gamma", ticker="SPY", got_px=100.0)
    assert ok.commits == 1 and ok.rollbacks == 0


def test_four_writers_same_txn_by_source():
    from analysis import day_bias, paper_trader, rs_leader_book, twotest_book
    fns = (
        paper_trader.run_trigger_loop,
        paper_trader.run_swing_close_settle,
        rs_leader_book.run_rsl_tick,
        twotest_book.run_tt_tick,
        day_bias.run_daybias_loop,
        day_bias.run_daybias_settle,
    )
    saw_entry = saw_exit = 0
    for fn in fns:
        src = inspect.getsource(fn)
        for block in src.split("conn.commit()"):
            if "INSERT INTO paper_trades" in block:
                assert "record_entry(" in block, fn.__name__
                assert "RETURNING id" in block, fn.__name__
                saw_entry += 1
            if ("UPDATE paper_trades" in block
                    and "exited_at" in block
                    and "SET legs=" not in block
                    and "SET shadow=" not in block):
                assert "record_exit(" in block, fn.__name__
                saw_exit += 1
    assert saw_entry == 4, saw_entry          # one INSERT path per writer
    assert saw_exit >= 4, saw_exit            # loop + settle on paper/day_bias


def test_singular_fill_audit_stays_forensic():
    from analysis import fill_audit, fills_audit
    assert "INSERT INTO fills_audit" not in inspect.getsource(fill_audit)
    src = inspect.getsource(fills_audit)
    assert "INSERT INTO paper_trades" not in src
    assert "UPDATE paper_trades" not in src
    assert INSERT_SQL.startswith("INSERT INTO fills_audit")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
