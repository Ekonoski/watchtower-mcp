"""One definition, enforced repo-wide (2026-09-02, the 11:09 phantom
exit ping — the book was cured of the partial-5m-block bug on 9/1, but
the Discord watcher carried its OWN COPY of the lifecycle and the copy
kept the bug: a second definition is a second place for the same bug
to live).

  1. No module under alerts/ may contain its own resample/trail math:
     the tokens that built the copy (_res5, e21_by_min, a local ema5)
     are forbidden there. Alert code that decides an rs_leader state
     must import rs_leader_book.lifecycle_state.
  2. The nightly audit reconciles 🚪 exit pings against the ledger:
     a ping before the recorded exit bar (phantom), a ping with no
     exit, and an exit with no ping are all anomalies.
"""
import datetime as dt
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN_IN_ALERTS = ("_res5", "res5(", "e21_by_min", "_ema as ema5")


def test_alert_modules_carry_no_second_lifecycle():
    offenders = []
    for path in glob.glob(os.path.join(ROOT, "alerts", "*.py")):
        src = open(path).read()
        for tok in FORBIDDEN_IN_ALERTS:
            if tok in src:
                offenders.append(f"{os.path.basename(path)}: {tok}")
        if "rs_leader" in src and "lifecycle" in src:
            assert "from analysis.rs_leader_book import lifecycle_state" in src, \
                f"{os.path.basename(path)} decides rs_leader state without the book's function"
    assert not offenders, f"second lifecycle definitions found: {offenders}"


def test_reconcile_pings_catches_every_divergence():
    from analysis.ledger_audit import reconcile_pings
    utc = dt.timezone.utc
    exit_bar = dt.datetime(2026, 9, 2, 15, 39, tzinfo=utc)      # 11:39 ET
    good_ping = exit_bar + dt.timedelta(seconds=72)             # 11:40:12
    phantom = exit_bar - dt.timedelta(minutes=30)               # 11:09
    trades = [("META", exit_bar, "trail")]
    assert reconcile_pings(trades, [good_ping]) == []           # clean
    out = reconcile_pings(trades, [phantom])
    assert out and "PRECEDES" in out[0]                         # the 9/2 case
    out = reconcile_pings(trades, [])
    assert out and "silent exit" in out[0]
    out = reconcile_pings([("META", None, None)], [good_ping])
    assert out and "phantom ping" in out[0]
    # eod_flat exits carry the bell, not a door — no ping expected
    assert reconcile_pings([("META", exit_bar, "eod_flat")], []) == []


def test_audit_run_wires_reconciliation():
    import inspect
    from analysis import ledger_audit
    src = inspect.getsource(ledger_audit.run)
    assert "reconcile_pings(trades, pings)" in src
    assert "kind='rsl_exit'" in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
