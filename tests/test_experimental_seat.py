"""The experimental seat on the leader board (2026-09-09 — Eric ruled
the seat test's tie in: "add PLTR as the 8th name experimental").

  1. The GRADED universe never moves: rsleader_study.TICKERS stays the
     seven, and the seat test's control still equals it. The LIVE
     universe is the book's LIVE_TICKERS = TICKERS + EXPERIMENTAL.
  2. One definition: the ping ranks through the book's rank_live (which
     wraps the study's rs_rank) over LIVE_TICKERS; the book's tick loops
     the same tuple. Neither carries a list of names of its own.
  3. The tag is everywhere: label() renders an experimental name as
     such and a graded name bare; an experimental leader names whom it
     displaced (or that the seven would have stood aside).
  4. A hole in the experimental name drops the seat, never the read.
  5. The scoreboard prints the seat's own record under its book every
     day — zero resolved is data.
  6. PLTR's bars never reach the graded record: it is appended to
     liquid_1m_bars, not mag7_1m_bars, so rsleader_study's grader (which
     ranks whatever mag7_1m_bars returns) still grades the control.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import desk_events, rsleader_ping as rp  # noqa: E402
from analysis import index_bars_daily, rs_leader_book as rb  # noqa: E402
from analysis import rsl_universe_study as u, rsleader_study as base  # noqa: E402


def test_graded_universe_untouched_and_live_universe_is_seven_plus_seat():
    assert base.TICKERS == ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
    assert rb.EXPERIMENTAL == ("PLTR",)
    assert rb.LIVE_TICKERS == base.TICKERS + ("PLTR",)
    assert u.universes()["mag7"] == tuple(base.TICKERS)        # the control
    assert set(rb.EXPERIMENTAL) <= set(rb.EXPERIMENTAL_PRIOR)   # a seat states its prior
    for name in ("AMD", "AVGO", "MU", "NFLX"):                  # sign-flipped by half: off
        assert name not in rb.LIVE_TICKERS


def test_one_definition_ping_and_book_rank_the_same_tuple():
    src_ping = inspect.getsource(rp)
    src_book = inspect.getsource(rb.run_rsl_tick)
    assert "from analysis.rs_leader_book import" in src_ping
    assert 'for tk in LIVE_TICKERS + ("QQQ",)' in inspect.getsource(rp._rank_now)
    assert 'for tk in LIVE_TICKERS + ("QQQ",)' in src_book
    assert "rank_live(" in inspect.getsource(rp._rank_now) and "rank_live(" in src_book
    assert '"PLTR"' not in src_ping                              # no list of its own
    assert "def rs_rank" not in inspect.getsource(rb)


def test_label_and_displacement():
    assert rb.label("PLTR") == "PLTR (experimental)"
    assert rb.label("META") == "META"
    # PLTR leads, META would have led the seven: displaced is named
    rets = {"AAPL": 0.1, "MSFT": 0.0, "NVDA": 0.2, "AMZN": 0.0, "GOOGL": 0.1,
            "META": 0.9, "TSLA": 0.3, "PLTR": 1.4}
    leader, _lag, _mid, rs, displaced = rb.rank_live(rets, 0.2)
    assert leader == "PLTR" and displaced == "META"
    # PLTR leads and no mag-7 name clears the bar: stand-aside stated
    rets["META"] = 0.3
    leader, _lag, _mid, rs, displaced = rb.rank_live(rets, 0.2)
    assert leader == "PLTR" and displaced is None
    # a graded leader carries no displacement
    rets["PLTR"] = 0.0
    leader, _lag, _mid, rs, displaced = rb.rank_live(rets, 0.2)
    assert leader is None and displaced is None
    rets["TSLA"] = 0.9
    assert rb.rank_live(rets, 0.2)[0] == "TSLA" and rb.rank_live(rets, 0.2)[4] is None


def test_a_hole_in_the_seat_drops_the_seat_not_the_read():
    src = inspect.getsource(rp._rank_now)
    assert "if tk in EXPERIMENTAL:" in src and "holes.append(tk)" in src
    src = inspect.getsource(rb.run_rsl_tick)
    assert "if tk in EXPERIMENTAL:" in src and "holes.append(tk)" in src
    # the seven's read still ranks when the eighth is missing
    rets = {t: 0.0 for t in base.TICKERS}
    rets["NVDA"] = 1.0
    assert rb.rank_live(rets, 0.1)[0] == "NVDA"


def test_posts_tag_the_seat_and_state_its_own_prior():
    src = inspect.getsource(rp.run_rank_ping)
    assert "label(leader)" in src and "EXPERIMENTAL SEAT" in src
    assert "EXPERIMENTAL_PRIOR[leader]" in src
    assert "displaced" in src and "stood aside" in src
    assert "bar hole" in src
    src = inspect.getsource(rp.run_go_watch)
    assert "label(leader)" in src and "EXPERIMENTAL SEAT" in src
    src = inspect.getsource(rp.run_trade_watch)
    assert "label(leader)" in src
    src = inspect.getsource(rb.run_rsl_tick)
    assert "EXPERIMENTAL SEAT" in src and "label(leader)" in src


def test_scoreboard_prints_the_seats_own_record_every_day():
    today = dt.date(2026, 9, 9)
    rows = [("rs_leader", 5, 3, 2, 1.20), ("swing", 10, 4, 6, -2.0)]
    out = desk_events.format_scoreboard(rows, [], today,
                                        experimental=[("rs_leader", "PLTR", 0, 0, 0, 0.0)])
    assert "↳ PLTR (experimental seat): 0 resolved" in out       # zero is data
    assert out.index("rs_leader: 5 resolved") < out.index("↳ PLTR")
    out = desk_events.format_scoreboard(rows, [], today,
                                        experimental=[("rs_leader", "PLTR", 2, 1, 1, 0.35)])
    assert "↳ PLTR (experimental seat): 2 resolved · 1-1 · +0.35R — own n" in out
    # the seat line lives under its book, never under another
    assert out.index("swing:") < out.index("rs_leader:") < out.index("↳ PLTR")
    src = inspect.getsource(desk_events.run_books_scoreboard)
    assert "from analysis.rs_leader_book import" in src and "EXPERIMENTAL" in src


def test_seat_bars_never_reach_the_graded_record():
    assert "PLTR" not in index_bars_daily.MAG7
    assert "PLTR" in index_bars_daily.LIQUID
    assert "mag7_1m_bars" in inspect.getsource(base._grade_day)
    assert "INSERT INTO mag7_1m_bars" not in inspect.getsource(rb)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
