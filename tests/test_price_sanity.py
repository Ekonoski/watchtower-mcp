"""daily_prices sanity sweep (2026-09-04, the SPY 2005-05-27 fat-finger):
the suspect rule, the verdicts, and the sweep's discipline — it writes
the vendor's bar or nothing, records every verdict, and touches no book.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import price_sanity as ps  # noqa: E402


def test_suspect_rule():
    assert ps.is_suspect(120.06, 1120.2, 119.8, 120.25) is True      # the SPY row
    assert ps.is_suspect(772.3, 774.1, 770.5, 771.8) is False        # a normal day
    assert ps.is_suspect(0.386, 12.06, 0.386, 11.98) is True         # a real warrant print: suspect, then CONFIRMED
    assert ps.is_suspect(5.0, 4.0, 4.5, 4.2) is True                 # high < low
    assert ps.is_suspect(None, 10.0, 9.0, 9.5) is False              # open missing is not a suspect
    assert ps.is_suspect(1.0, 1.0, 0.0, 0.0) is False                # zero-priced rows are excluded, not judged


def test_verdicts():
    old = (120.06, 1120.2, 119.8, 120.25)
    assert ps.verdict(old, (120.06, 120.2, 119.8, 120.25)) == "corrected"
    assert ps.verdict(old, old) == "confirmed"
    assert ps.verdict(old, None) == "no_vendor_bar"
    assert ps.verdict((None, 1.0, 0.9, 0.95), (None, 1.0, 0.9, 0.95)) == "confirmed"


def test_vendor_anomalies_are_flagged_not_edited():
    """2026-09-04: the vendor CONFIRMED five impossible prints; Eric ruled
    them flagged. The migration adds the verdict and a view that nulls
    O/H/L on flagged rows; the oscillator's daily fetch reads the view
    (its COALESCE to close already handles the NULL). The raw table is
    never written by the flag path."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mig = open(os.path.join(root, "migrations", "056_vendor_anomalies.sql")).read()
    assert "'vendor_anomaly'" in mig and "CREATE OR REPLACE VIEW daily_prices_clean" in mig
    assert "UPDATE daily_prices" not in mig
    for tk, d in (("SPY", "2005-05-27"), ("SPY", "2006-01-26"), ("SPY", "2008-09-29"),
                  ("IWM", "2008-09-19"), ("IWM", "2009-06-16")):
        assert f"('{tk}', DATE '{d}')" in mig
    osc = open(os.path.join(root, "analysis", "oscillator.py")).read()
    assert "FROM daily_prices_clean" in osc


def test_writes_vendor_bar_or_nothing():
    src = inspect.getsource(ps.run)
    assert 'if v == "corrected":' in src and "UPDATE daily_prices SET open=%s, high=%s, low=%s, close=%s" in src
    assert "INSERT INTO price_sanity" in src
    whole = inspect.getsource(ps)
    for forbidden in ("paper_", "trade_journal", "DELETE FROM"):
        assert forbidden not in whole
    # the SQL rule and the pure rule agree on the ratio
    assert "%(r)s * d.close" in ps.SUSPECT_SQL and ps.RATIO == 2.0
    sched = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "alerts", "scheduler.py")).read()
    assert '"analysis.price_sanity"' in sched


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
