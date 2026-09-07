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
    # the only UPDATE in the module is the journal's own config upsert (set_config);
    # nothing here may touch the desk's books
    cfg = inspect.getsource(trade_journal.set_config)
    assert "INSERT INTO journal_config" in cfg and "DO UPDATE SET" in cfg
    rest = src.replace(cfg, "")
    assert "UPDATE " not in rest and "DELETE FROM" not in rest and "DELETE FROM" not in cfg


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


def test_scoreboard_vs_spy():
    """The human scoreboard (2026-09-07): account = base + realized P&L on
    the exit date; SPY total return with dividends reinvested at the
    ex-date close; both clauses of the Beat-SPY rule; exits after the last
    bar are stated, never booked early."""
    import datetime as dt
    from analysis.trade_journal import scoreboard, set_config
    d = [dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(10)]
    closes = [100.0, 102.0, 101.0, 99.0, 100.0, 103.0, 104.0, 102.0, 105.0, 106.0]
    divs = {d[5]: 1.03}                                       # 1% yield paid on day 6
    trades = [(d[2], 500.0), (d[3], -800.0), (d[6], 1200.0), (dt.date(2026, 9, 20), 400.0)]
    sb = scoreboard(25_000.0, trades, d, closes, divs, d[0], d[-1])
    assert sb["days"] == 10 and sb["first"] == d[0] and sb["last"] == d[-1]
    assert abs(sb["sys_eq"] - 25_900.0) < 1e-9                 # +500 -800 +1200; the 9/20 exit is NOT booked
    assert abs(sb["unbooked"] - 400.0) < 1e-9
    assert abs(sb["sys_ret"] - 0.036) < 1e-9
    # SPY: 250 units, +1% reinvested on day 6 at 103 -> 252.5 units * 106
    assert abs(sb["spy_eq"] - (250 * 1.01 * 106.0)) < 1e-6
    assert abs(sb["spy_dd"] - (99.0 / 102.0 - 1.0)) < 1e-9       # SPY's worst: 102 -> 99
    assert abs(sb["sys_dd"] - (24_700.0 / 25_500.0 - 1.0)) < 1e-9  # account: 25,500 -> 24,700
    assert sb["clause_return"] is False and sb["clause_dd"] is False and sb["beats"] is False   # -3.1% is deeper than -2.9%
    sb2 = scoreboard(25_000.0, [(d[2], 3000.0)], d, closes, divs, d[0], d[-1])
    assert sb2["clause_return"] is True and sb2["clause_dd"] is True and sb2["beats"] is True   # +12%, no drawdown
    assert scoreboard(25_000.0, trades, d, closes, divs, dt.date(2027, 1, 1), dt.date(2027, 2, 1)) is None
    # config keys are a closed set and starting equity must be positive
    for bad in (("nonsense", "1"), ("starting_equity", "-5"), ("scoreboard_start", "not-a-date")):
        try:
            set_config(*bad)
            assert False, bad
        except ValueError:
            pass
    src = inspect.getsource(trade_journal._scoreboard_block)
    assert "ASSUMED" in src and "unavailable" in src               # the base is labeled; a missing input is a hole


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
