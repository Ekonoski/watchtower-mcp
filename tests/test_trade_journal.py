"""Eric's trade journal, pinned (2026-09-02).

  1. R math: underlying terms, sign-correct for shorts, computed only
     when entry/stop/exit all exist — never fabricated.
  2. Validation refuses junk (direction, timestamps) with reasons.
  3. Writes only trade_journal — by signature (his book, never the
     desk's).
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import trade_journal  # noqa: E402
from analysis.trade_journal import _num, _parse_ts  # noqa: E402


def test_r_math_two_readings():
    from analysis.trade_journal import R_DOLLARS, r_readings
    assert R_DOLLARS == 250.0
    # dollar P&L present: baseline R on $250 and real-risk R on the stop's dollars
    assert r_readings(412.0, 200.0, "long", None, None, None) == (1.65, 2.06)
    # no risk_dollars -> real-risk R is a hole, never zero
    assert r_readings(-192.0, None, "long", None, None, None) == (-0.77, None)
    # no dollar P&L: underlying-price R, sign-correct for shorts; all three or nothing
    assert r_readings(None, None, "long", 100.0, 102.0, 99.0) == (2.0, None)
    assert r_readings(None, None, "short", 100.0, 98.0, 101.0) == (2.0, None)
    assert r_readings(None, None, "long", 100.0, 102.0, None) == (None, None)
    src = inspect.getsource(trade_journal.log_trade)
    assert "r_readings(pnl, risk, direction, e_px, x_px, s_px)" in src
    assert "risk_dollars, r_actual" in src        # both columns written


def test_validation_refuses_junk():
    try:
        _parse_ts("not a time")
        assert False, "should have raised"
    except ValueError as e:
        assert "ISO" in str(e)
    assert _parse_ts("") is None
    assert _parse_ts("2026-09-02T10:35") is not None
    try:
        _num("abc", "entry_px")
        assert False, "should have raised"
    except ValueError as e:
        assert "entry_px" in str(e)
    assert _num(None, "x") is None and _num("1.5", "x") == 1.5


def test_writes_only_the_journal():
    src = inspect.getsource(trade_journal)
    assert "INSERT INTO trade_journal" in src
    assert "INSERT INTO paper_" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


def test_skip_is_a_decision_not_a_trade():
    """2026-09-04, the NVDA GO Eric declined: a skip records kind='skip'
    with its reason and the declined spec, writes NO P&L / R columns,
    and the R aggregates read kind='trade' only."""
    from analysis.trade_journal import log_skip
    src = inspect.getsource(log_skip)
    assert "VALUES ('skip'" in src
    for col in ("pnl_dollars", "r_multiple", "r_actual", "entry_px"):
        assert col not in src, f"a skip must not write {col}"
    assert "skip_reason, spec_id" in src
    assert "reason is required" in src            # an unexplained skip is refused
    summ = inspect.getsource(trade_journal.journal_summary)
    assert "WHERE kind = 'trade'" in summ          # R math excludes skips
    assert "_skips_block(c, days)" in summ         # and the skips still render
    blk = inspect.getsource(trade_journal._skips_block)
    assert "LEFT JOIN paper_specs" in blk and "LEFT JOIN paper_trades" in blk
    assert "no fill" in blk and "hole" in blk      # unfilled / unlinked are not zeros
    # the summary's zero-trade branch still prints the skips
    assert "skip_lines" in summ.split("if not rows:")[1].split("closed = ")[0]


def test_chart_links_ride_the_row():
    """2026-09-04 (Eric: "are you also logging the charts?") — a pasted
    screenshot lives only in a session; the row carries LINKS to the
    charts in his Drive folder, on trades and skips alike, rendered
    beside the row."""
    from analysis.trade_journal import _urls
    assert _urls("") is None and _urls([]) is None
    assert _urls("https://a/1, https://a/2") == ["https://a/1", "https://a/2"]
    assert _urls(["https://a/1", " "]) == ["https://a/1"]
    assert "chart_urls" in inspect.getsource(trade_journal.log_trade)
    assert "chart_urls" in inspect.getsource(trade_journal.log_skip)
    summ = inspect.getsource(trade_journal.journal_summary)
    assert "📎" in summ and "📎" in inspect.getsource(trade_journal._skips_block)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
