"""The two-test book, pinned (2026-09-17, Eric: "build the two-test
book" — the trade he executes by hand, three correct PLTR skips in).

  1. ONE DEFINITION: the entry machine is twotest_study.two_test +
     tapeentry_study.level_machine / resample5 with the study's clocks
     and STOP_BUFF; the leader is the GO book's spec; the levels are
     rs_leader_book.level_inputs; the exit is lifecycle_state_v2 +
     select_levels. Nothing re-implemented in the book or the ping.
  2. completed5: the trailing PARTIAL 5m block never confirms or kills
     (the 2026-09-01 lesson); a completed one does.
  3. level_state on a constructed tape: no_confirm → confirmed →
     forming → triggered, with L1/H/L2 where the study puts them; the
     fill is H, or the OPEN when the trigger bar gapped through H; the
     stop is L2 − 0.05%; a 5m close back through the level kills it.
  4. day_state: the earliest trigger wins; ties go to PDH.
  5. shadow_graded = the study's expression (5m-close stop + bell).
  6. Birth: migration 074 (book, fill_kind 'cross', shadow column), the
     poller exclusion, the audit vocabulary, the scheduler jobs, the
     ping's read-only signature and claim kinds.
  7. The tick end to end on a fake connection: arm from the leader spec
     (and stand aside when the leader stood aside), fill with frozen
     levels, exit, and the shadow written only at the bell.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from alerts import twotest_ping as tp  # noqa: E402
from analysis import twotest_book as tb  # noqa: E402


def _bars(specs, t0=dt.datetime(2026, 9, 17, 9, 30)):
    return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]


def _flat(px, n):
    return [(px, px + 0.05, px - 0.05, px)] * n


def test_one_definition_imported():
    src = inspect.getsource(tb)
    for needle in ("from analysis.twotest_study import", "two_test", "level_machine",
                   "resample5", "STOP_BUFF", "CONFIRM_START", "CONFIRM_END", "TRIGGER_END",
                   "from analysis.rs_leader_book import", "lifecycle_state_v2", "select_levels",
                   "level_inputs", "_persist_1m"):
        assert needle in src
    for banned in ("def two_test", "def level_machine", "def resample5", "def pivots",
                   "def lifecycle_state", "def select_levels", "def rs_rank"):
        assert banned not in src
    assert tb.FAMILIES == ("pdh", "pmh")               # ORB flat on leader days; no shorts
    src_p = inspect.getsource(tp)
    assert "from analysis.twotest_book import" in src_p and "day_state" in src_p
    assert "from analysis.rs_leader_book import lifecycle_state_v2" in src_p
    assert "INSERT INTO" not in src_p and "UPDATE " not in src_p and "DELETE FROM" not in src_p
    assert "claim_and_send" in src_p


def test_completed5_drops_the_trailing_partial_block():
    b = _bars(_flat(100, 12))                          # 9:30 .. 9:41
    bars5, last1m = tb.completed5(b)
    assert len(bars5) == 2 and last1m == [4, 9]         # 9:30-34, 9:35-39; 9:40-41 partial
    b = _bars(_flat(100, 10))                          # 9:30 .. 9:39: both complete
    bars5, last1m = tb.completed5(b)
    assert len(bars5) == 2 and last1m == [4, 9]


def _tape():
    """PDH 100.00. 9:30-9:39 inside (closes 99.6-99.8); the 9:40-9:44
    block CLOSES 100.30 (confirmed at 9:44); then on the 1m: L1 at 9:47
    (low 100.05), H at 9:49 (high 100.60), L2 at 9:52 (low 100.20, above
    L1 and the level), then 9:55 crosses H (high 100.75)."""
    specs = [(99.7, 99.8, 99.5, 99.6)] * 10                       # 9:30-9:39 inside
    specs += [(99.6, 99.9, 99.5, 99.8), (99.8, 100.1, 99.7, 100.0),  # 9:40, 9:41
              (100.0, 100.3, 99.9, 100.2), (100.2, 100.4, 100.1, 100.3),
              (100.3, 100.4, 100.2, 100.3)]                       # 9:42-9:44, block close 100.30
    specs += [(100.3, 100.4, 100.2, 100.3),        # 9:45
              (100.3, 100.35, 100.15, 100.2),      # 9:46
              (100.2, 100.25, 100.05, 100.1),      # 9:47  L1 low 100.05
              (100.1, 100.4, 100.1, 100.35),       # 9:48
              (100.35, 100.6, 100.3, 100.5),       # 9:49  H high 100.60
              (100.5, 100.55, 100.35, 100.4),      # 9:50
              (100.4, 100.45, 100.25, 100.3),      # 9:51
              (100.3, 100.35, 100.2, 100.25),      # 9:52  L2 low 100.20
              (100.25, 100.45, 100.22, 100.4),     # 9:53  confirms L2
              (100.4, 100.5, 100.35, 100.45),      # 9:54
              (100.45, 100.75, 100.4, 100.7)]      # 9:55  crosses H -> trigger
    return _bars(specs)


def test_level_state_walks_the_study_machine():
    b = _tape()
    assert tb.level_state(b[:10], 100.0)["status"] == "no_confirm"
    # the 9:40-9:44 block is PARTIAL at 9:43 -> still no confirmation
    assert tb.level_state(b[:14], 100.0)["status"] == "no_confirm"
    st = tb.level_state(b[:15], 100.0)                 # 9:44 printed: confirmed, no 1m after it
    assert st["status"] == "confirmed" and st["confirm_ts"].time() == dt.time(9, 44)
    st = tb.level_state(b[:24], 100.0)                 # through 9:53: L2 confirmed, no trigger yet
    assert st["status"] == "forming" and (st["l1"], st["h"], st["l2"]) == (100.05, 100.6, 100.2)
    st = tb.level_state(b, 100.0)
    assert st["status"] == "triggered" and st["i_trig"] == 25 and b[25][0].time() == dt.time(9, 55)
    assert st["entry"] == 100.6 and st["gap_fill"] is False
    assert abs(st["stop"] - 100.2 * (1 - 0.0005)) < 1e-9
    # the trigger bar OPENED above H -> the fill is the open
    b2 = b[:25] + [(b[25][0], 100.65, 100.75, 100.6, 100.7)]
    st = tb.level_state(b2, 100.0)
    assert st["status"] == "triggered" and st["entry"] == 100.65 and st["gap_fill"] is True
    # a completed 5m close back through the level before the trigger kills it
    b3 = b[:20] + [(b[20][0] + dt.timedelta(minutes=k), 100.2, 100.25, 99.7, 99.8)
                   for k in range(5)]                  # 9:50-9:54 closes 99.80 < 100
    st = tb.level_state(b3, 100.0)
    assert st["status"] == "structure_failed" and st["why"] == "level_lost"
    assert tb.level_state(b, None)["status"] == "no_level"


def test_day_state_picks_the_earliest_trigger_and_pdh_on_ties():
    b = _tape()
    st = tb.day_state(b, 100.0, None)
    assert st["trigger"]["family"] == "pdh" and st["families"]["pmh"]["status"] == "no_level"
    st = tb.day_state(b, 100.0, 100.0)                 # both levels identical: PDH wins the tie
    assert st["trigger"]["family"] == "pdh"
    st = tb.day_state(b, 100.0, 99.0)                  # PMH 99: inside never seen -> no_confirm
    assert st["families"]["pmh"]["status"] == "no_confirm" and st["trigger"]["family"] == "pdh"
    txt = tb.verdict_text(st["families"])
    assert "PDH: triggered" in txt and "PMH: no_confirm" in txt


def test_shadow_is_the_graded_expression():
    b = _tape() + _bars(_flat(101.0, 10), dt.datetime(2026, 9, 17, 9, 56))
    st = tb.level_state(b, 100.0)
    sh = tb.shadow_graded(b, st["i_trig"], st["entry"], st["stop"])
    assert sh["reason"] == "eod_flat" and sh["exit_px"] == 101.0
    assert abs(sh["r"] - (101.0 - 100.6) / (100.6 - st["stop"])) < 1e-4   # r is rounded to 4 places
    # no bars after the trigger -> a hole, never a number
    assert tb.shadow_graded(b[:st["i_trig"] + 1], st["i_trig"], st["entry"], st["stop"]) is None


def test_book_birth_touches_every_place():
    from analysis import ledger_audit, paper_trader
    mig = open(os.path.join(ROOT, "migrations", "074_two_test.sql")).read()
    for needle in ("'two_test'::text", "'cross'::text", "ADD COLUMN IF NOT EXISTS shadow jsonb"):
        assert needle in mig
    assert ledger_audit.LEGAL_EXITS["two_test"] == ledger_audit.LEGAL_EXITS["rs_leader_v2"]
    assert "'two_test'" in inspect.getsource(paper_trader)
    # the scheduler is read as a file: importing it pulls the screen
    # modules (numpy) into a test that needs neither
    src = open(os.path.join(ROOT, "alerts", "scheduler.py")).read()
    for needle in ("from analysis.twotest_book import run_tt_tick", "from alerts.twotest_ping import run_tt_watch",
                   'id="tt_book_day"', 'id="tt_watch_day"', 'id="tt_book_settle"'):
        assert needle in src
    assert tb.BOOK == "two_test" and tb.SETUP == "tt_leader_level"
    src_b = inspect.getsource(tb)
    assert "fill_kind, confirm_status)" in src_b and "'cross'" in src_b
    assert "SET shadow=%s::jsonb" in src_b and "DELETE FROM" not in src_b
    for other in ("'day_bias'", "'swing'", "'gamma'", "'rs_leader'", "'rs_leader_v2'"):
        assert other not in src_b                      # the leader book's name comes from its module
    assert {tp.KIND_CONFIRM, tp.KIND_GO, tp.KIND_TP1, tp.KIND_RATCHET, tp.KIND_EXIT,
            tp.KIND_BELL, tp.KIND_DONE} == {"tt_confirm", "tt_go", "tt_tp1", "tt_ratchet",
                                           "tt_exit", "tt_bell", "tt_done"}


# ── the tick on a fake connection ─────────────────────────────────────

class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.conn.log.append((s, params))
        self._last = s

    def fetchone(self):
        s = self._last
        if "INSERT INTO paper_trades" in s:
            return (1,)
        if s.startswith("SELECT id, ticker, status, stop, levels FROM paper_specs"):
            return self.conn.spec
        if s.startswith("SELECT ticker, status FROM paper_specs"):
            return self.conn.leader
        if "FROM paper_trades WHERE spec_id=" in s:
            return self.conn.trade
        return None


class _Conn:
    def __init__(self, spec, leader, trade):
        self.spec, self.leader, self.trade = spec, leader, trade
        self.log, self.commits = [], 0

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _run(spec, leader, trade, now_hm, bars):
    import json
    from zoneinfo import ZoneInfo
    from analysis import paper_trader
    et = ZoneInfo("America/New_York")
    conn = _Conn(spec, leader, trade)
    bars = [(t.replace(tzinfo=et), o, h, l, c) for t, o, h, l, c in bars]

    class _Now(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            hh, mm = now_hm.split(":")
            return dt.datetime(2026, 9, 17, int(hh), int(mm), 45, tzinfo=et)

    saved = (paper_trader.get_db_connection, tb._persist_1m, tb.level_inputs, tb.dt)
    try:
        paper_trader.get_db_connection = lambda: conn
        tb._persist_1m = lambda c, tk, day: bars
        tb.level_inputs = lambda c, tk, day: (100.0, None)
        tb.dt = type("dtmod", (), {"datetime": _Now, "time": dt.time,
                                   "timedelta": dt.timedelta, "timezone": dt.timezone})
        tb.run_tt_tick()
    finally:
        paper_trader.get_db_connection, tb._persist_1m, tb.level_inputs, tb.dt = saved
    return conn, json


def test_tick_arms_from_the_leader_fills_exits_and_shadows():
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    b = _tape()
    # A. no two-test spec yet, the leader book armed on META -> arm, levels frozen (PMH a hole)
    conn, json = _run(None, ("META", "armed"), None, "09:47", b[:17])
    ins = [(s, p) for s, p in conn.log if s.startswith("INSERT INTO paper_specs")]
    assert len(ins) == 1 and ins[0][1][1] == "two_test" and ins[0][1][2] == "META"
    assert json.loads(ins[0][1][5]) == {"pdh": 100.0, "pmh": None, "holes": ["pmh"]}
    assert "Level holes: pmh" in ins[0][1][4]
    # A'. the leader book stood aside -> a skipped_rank row, zero is data
    conn, _ = _run(None, ("—", "skipped_rank"), None, "09:47", b[:17])
    ins = [(s, p) for s, p in conn.log if s.startswith("INSERT INTO paper_specs")]
    assert len(ins) == 1 and "'skipped_rank'" in ins[0][0]
    # B. armed, bars through 9:55 -> the trigger fills at H with frozen TP levels
    lv = {"pdh": 100.0, "pmh": None, "holes": ["pmh"]}
    conn, json = _run((900, "META", "armed", 0, lv), None, None, "09:56", b)
    tr = [(s, p) for s, p in conn.log if s.startswith("INSERT INTO paper_trades")]
    up = [(s, p) for s, p in conn.log if s.startswith("UPDATE paper_specs SET status='triggered'")]
    assert len(tr) == 1 and tr[0][1][2] == 100.6 and tr[0][1][1].time() == dt.time(9, 55)
    assert "'cross'" in tr[0][0]
    s, p = up[0]
    entry, stop, target, setup, lv2_js, txt, sid = p
    lv2 = json.loads(lv2_js)
    assert entry == 100.6 and abs(stop - 100.2 * (1 - 0.0005)) < 1e-9 and setup == "tt_pdh"
    assert lv2["l1"] == 100.05 and lv2["h"] == 100.6 and lv2["l2"] == 100.2 and lv2["family"] == "pdh"
    assert "TRIGGER 09:55 on PDH" in txt and sid == 900
    # C. triggered, open; a 5m block later closes under the stop -> exit 'stop'; no shadow before the bell
    lv2["tp1"] = {"px": 101.2, "kind": "hod"}; lv2["tp2"] = None
    trade = (777, dt.datetime(2026, 9, 17, 9, 55, tzinfo=et), 100.6, None, None, None)
    b2 = b + _bars([(100.7, 100.75, 99.9, 100.0)] * 5, dt.datetime(2026, 9, 17, 9, 56))  # 9:56-10:00
    b2 += _bars([(100.0, 100.05, 99.95, 100.0)] * 4, dt.datetime(2026, 9, 17, 10, 1))    # 10:01-10:04
    conn, json = _run((900, "META", "triggered", stop, lv2), None, trade, "10:05", b2)
    ex = [(s, p) for s, p in conn.log if s.startswith("UPDATE paper_trades SET exited_at=")]
    assert len(ex) == 1 and ex[0][1][2] == "stop" and ex[0][1][1] == 100.0
    assert not any("SET shadow=" in s for s, _ in conn.log)
    # D. after the bell the exited trade gets its shadow (the graded expression), once
    trade_x = (777, trade[1], 100.6, dt.datetime(2026, 9, 17, 10, 4, tzinfo=et), [], None)
    conn, json = _run((900, "META", "triggered", stop, lv2), None, trade_x, "16:00", b2)
    sh = [(s, p) for s, p in conn.log if s.startswith("UPDATE paper_trades SET shadow=")]
    assert len(sh) == 1 and json.loads(sh[0][1][0])["graded_eod"]["reason"] == "stop"
    trade_x = trade_x[:5] + ({"graded_eod": {}},)
    conn, _ = _run((900, "META", "triggered", stop, lv2), None, trade_x, "16:00", b2)
    assert not any("SET shadow=" in s for s, _ in conn.log) and conn.commits == 0
    # E. no trigger by 15:00 -> cancelled with the per-level verdict
    conn, _ = _run((900, "META", "armed", 0, lv), None, None, "15:01", b[:24])
    cx = [(s, p) for s, p in conn.log if s.startswith("UPDATE paper_specs SET status='cancelled'")]
    assert len(cx) == 1 and "PDH: forming" in cx[0][1][0] or "PDH: no_trigger" in cx[0][1][0]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
