"""rs_leader v2 — Eric's exit, pinned (2026-09-15: the desk's META trade
printed +3.0R at its high and the 21-EMA trail took it out −2.01R;
"a plus three R to negative two... poor trade management" → "build
that").

  1. select_levels: the graded level math IMPORTED (naive_levels /
     pick_targets), the 40-bp floor honoured, levels within 0.1% merged
     into one (ORB high == session high must never make TP2 == TP1),
     PDH/PMH holes named, the strike grid a NAMED last resort only.
  2. lifecycle_state_v2: struct stop on a completed 5m CLOSE (a wick is
     not a stop) and the disaster touch until the first partial; TP1 on
     touch takes half and moves the runner's stop to entry on TOUCH; the
     stop ratchets to each completed 5m low; TP2 takes the rest; the
     bell closes survivors only when `final`; the runner's stop decides
     before the disaster; reasons name what took the RUNNER out.
  3. The book's lifecycle agrees with the graded engine (sim_exit,
     be='touch' + ratchet='5mlow') on a tape where the struct stop never
     fires — one definition, proven by equality.
  4. META 2026-09-15, replayed from the RECORDED rsl_book_bars: v1 exited
     667.28 (−2.01R); v2 banks half at 675.50 at 10:00, ratchets to
     672.12 after the 10:00–10:04 block, and the runner touches out at
     10:06 — +0.83R, reason tp1_ratchet.
  5. Book birth: migration 073 admits the book, skipped_rank, the four
     runner reasons, legs/levels; the audit's exit vocabulary and the
     poller exclusion know the name; the ledger writes legs and levels.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from analysis import exit_shape_study as xs, rs_leader_book as rb  # noqa: E402


def _bars(specs, t0=dt.datetime(2026, 9, 15, 9, 45)):
    return [(t0 + dt.timedelta(minutes=i), *s) for i, s in enumerate(specs)]


# ── 1. levels ─────────────────────────────────────────────────────────

def test_select_levels_floor_merge_holes_and_last_resort():
    src = inspect.getsource(rb)
    assert "from analysis.exit_shape_study import naive_levels, pick_targets" in src
    assert "def naive_levels" not in src and "def pick_targets" not in src
    # 15 opening bars with high 101.0, then the GO bar at index 20
    specs = [(100, 101.0, 99.5, 100.2)] * 15 + [(100.2, 100.6, 99.9, 100.3)] * 6
    b = _bars(specs, dt.datetime(2026, 9, 15, 9, 30))
    entry = 100.3
    lv = rb.select_levels(b, 20, entry, pdh=102.0, pmh=101.05)
    # ORB high 101.0 == session high 101.0 (merged), PMH 101.05 within
    # 0.1% of it (merged too) -> ONE level at 101.0, then PDH 102.0
    assert lv["tp1"] == {"px": 101.0, "kind": "orh/hod/pmh"} or lv["tp1"]["px"] == 101.0
    assert "orh" in lv["tp1"]["kind"] and "hod" in lv["tp1"]["kind"]
    assert lv["tp2"] == {"px": 102.0, "kind": "pdh"}
    assert lv["holes"] == [] and lv["fallback"] is False
    # the floor: a level 20 bps above entry is not a target
    lv = rb.select_levels(b, 20, 100.9, pdh=102.0, pmh=None)
    assert lv["tp1"] == {"px": 102.0, "kind": "pdh"} and lv["tp2"] is None
    assert lv["holes"] == ["pmh"]
    # nothing structural in reach -> the strike grid, NAMED, no TP2
    lv = rb.select_levels(b, 20, 100.9, pdh=None, pmh=None)
    assert lv["fallback"] is True and lv["tp1"]["kind"] == "strike"
    assert lv["tp1"]["px"] == 102.5 and lv["tp2"] is None       # $2.50 grid at $100+: floor 101.30 -> 102.5
    assert lv["holes"] == ["pdh", "pmh"]
    assert "LAST RESORT" in rb.describe_levels(lv)
    assert "no TP2" in rb.describe_levels(lv)
    assert "NO TP1" in rb.describe_levels({"tp1": None})


# ── 2. the lifecycle ──────────────────────────────────────────────────

def test_lifecycle_v2_struct_stop_wick_rule_and_disaster():
    entry, stop, tp1 = 100.0, 99.5, 101.0
    # wicks under the stop, every 5m block CLOSES above it -> holding
    b = _bars([(100, 100.2, 99.3, 100.1)] * 10)
    st = rb.lifecycle_state_v2(b, 0, entry, stop, tp1)
    assert st["exit"] is None and st["legs"] == [] and st["stop_mode"] == "close"
    assert st["stop"] == stop
    # a completed block closing under the stop exits at the CLOSE — the
    # 9:45-9:49 block completes at 9:49 (index 4)
    b = _bars([(100, 100.1, 99.3, 99.4)] * 6)
    st = rb.lifecycle_state_v2(b, 0, entry, stop, tp1)
    assert st["exit"][0] == "stop" and st["exit"][2] == 99.4
    assert st["legs"] == [(1.0, 99.4, b[4][0], "stop")]
    # the same tape one bar short of the block's last minute: no exit
    st = rb.lifecycle_state_v2(b[:4], 0, entry, stop, tp1)
    assert st["exit"] is None
    # the disaster touch wins mid-block
    b = _bars([(100, 100.1, 99.9, 100.0)] + [(99.5, 99.6, 98.9, 99.4)] * 3)
    st = rb.lifecycle_state_v2(b, 0, entry, stop, tp1)
    assert st["exit"][0] == "disaster" and st["exit"][2] == 99.0


def test_lifecycle_v2_half_breakeven_ratchet_tp2_and_bell():
    entry, stop, tp1, tp2 = 100.0, 99.5, 101.0, 102.0
    specs = [(100, 100.2, 99.9, 100.1)] * 4                  # 9:45-9:48
    specs += [(100.5, 101.2, 100.4, 101.0)]                  # 9:49: TP1 touched
    specs += [(101.0, 101.1, 100.6, 100.9)] * 5              # 9:50-9:54 block, low 100.6
    b = _bars(specs)
    st = rb.lifecycle_state_v2(b, 0, entry, stop, tp1, tp2)
    assert st["tp1_hit"] and st["legs"] == [(0.5, 101.0, b[4][0], "tp1")]
    assert st["stop_mode"] == "touch"
    # the 9:45-9:49 block completed at 9:49 with low 99.9 -> ratchet is
    # max(entry, 99.9) = entry; the 9:50-9:54 block lifts it to 100.6
    assert st["stop"] == 100.6 and st["exit"] is None
    # a WICK to the ratcheted stop takes the runner out (touch-honoured)
    b2 = b + _bars([(100.9, 101.0, 100.55, 100.9)], b[-1][0] + dt.timedelta(minutes=1))
    st = rb.lifecycle_state_v2(b2, 0, entry, stop, tp1, tp2)
    assert st["exit"][0] == "tp1_ratchet"
    assert st["legs"][-1] == (0.5, 100.6, b2[-1][0], "runner_stop")
    assert abs(st["exit"][2] - (101.0 + 100.6) / 2) < 1e-9
    # breakeven: a touch of entry before any ratchet above it
    specs = [(100, 100.2, 99.9, 100.1)] * 4 + [(100.5, 101.2, 100.4, 101.0)]
    specs += [(100.8, 100.9, 99.95, 100.2)]                  # 9:50: entry touched
    st = rb.lifecycle_state_v2(_bars(specs), 0, entry, stop, tp1, tp2)
    assert st["exit"][0] == "tp1_be" and st["legs"][-1][1] == 100.0
    # TP2 takes the rest
    specs = [(100, 100.2, 99.9, 100.1)] * 4 + [(100.5, 101.2, 100.4, 101.0)]
    specs += [(101.5, 102.3, 101.4, 102.0)]
    st = rb.lifecycle_state_v2(_bars(specs), 0, entry, stop, tp1, tp2)
    assert st["exit"][0] == "tp1_tp2" and abs(st["exit"][2] - 101.5) < 1e-9
    # no TP2: the runner rides; only the bell (final) closes it
    st = rb.lifecycle_state_v2(_bars(specs), 0, entry, stop, tp1, None)
    assert st["exit"] is None and st["tp1_hit"]
    st = rb.lifecycle_state_v2(_bars(specs), 0, entry, stop, tp1, None, final=True)
    assert st["exit"][0] == "tp1_eod" and st["legs"][-1] == (0.5, 102.0, _bars(specs)[-1][0], "bell")
    # never a partial before TP1; the bell on an untouched trade is eod_flat
    flat = _bars([(100, 100.2, 99.9, 100.1)] * 7)
    st = rb.lifecycle_state_v2(flat, 0, entry, stop, tp1, tp2, final=True)
    assert st["exit"][0] == "eod_flat" and st["legs"] == [(1.0, 100.1, flat[-1][0], "bell")]
    # the runner's stop decides BEFORE the disaster in a bar through both
    specs = [(100, 100.2, 99.9, 100.1)] * 4 + [(100.5, 101.2, 100.4, 101.0)]
    specs += [(100.3, 100.4, 98.5, 98.6)]
    st = rb.lifecycle_state_v2(_bars(specs), 0, entry, stop, tp1, tp2)
    assert st["exit"][0] == "tp1_be" and st["legs"][-1][1] == entry


# ── 3. one definition: equality with the graded engine ────────────────

def test_lifecycle_v2_matches_sim_exit_where_the_struct_stop_is_silent():
    entry, tp1, tp2 = 100.0, 100.9, 102.0
    specs = [(100, 100.1, 99.9, 100.0)] * 5
    specs += [(100.5, 101.2, 100.4, 101.0)] * 5              # TP1 touched
    specs += [(100.3, 100.4, 99.9, 100.2)] * 5               # wick under entry -> be touch
    specs += [(101.0, 102.1, 100.9, 102.0)] * 5
    b = _bars(specs, dt.datetime(2026, 9, 2, 9, 45))
    ours = rb.lifecycle_state_v2(b, 0, entry, 99.0, tp1, tp2, final=True)
    ref = xs.sim_exit(b, 0, entry, tp1=tp1, tp1_frac=0.5, be="touch", ratchet="5mlow", tp2=tp2)
    assert [(f, p, t) for f, p, t, _w in ours["legs"]] == ref["legs"]
    assert ours["exit"][2] == ref["exit_px"] and ref["out"] == "tp1+stop_touch"
    # the ratchet path, TP2 reached
    specs = [(100, 100.1, 99.9, 100.0)] * 5
    specs += [(100.5, 101.2, 100.4, 101.0)] * 5
    specs += [(101.0, 101.3, 100.8, 101.1)] * 5
    specs += [(101.2, 102.1, 101.0, 102.0)] * 5
    b = _bars(specs, dt.datetime(2026, 9, 2, 9, 45))
    ours = rb.lifecycle_state_v2(b, 0, entry, 99.0, tp1, tp2, final=True)
    ref = xs.sim_exit(b, 0, entry, tp1=tp1, tp1_frac=0.5, be="touch", ratchet="5mlow", tp2=tp2)
    assert [(f, p, t) for f, p, t, _w in ours["legs"]] == ref["legs"]
    assert ref["out"] == "tp1+tp2" and ours["exit"][0] == "tp1_tp2"
    # the bell
    specs = [(100, 100.1, 99.9, 100.0)] * 5 + [(100.5, 101.2, 100.4, 101.0)] * 5
    specs += [(101.0, 101.3, 100.8, 101.1)] * 5
    b = _bars(specs, dt.datetime(2026, 9, 2, 9, 45))
    ours = rb.lifecycle_state_v2(b, 0, entry, 99.0, tp1, None, final=True)
    ref = xs.sim_exit(b, 0, entry, tp1=tp1, tp1_frac=0.5, be="touch", ratchet="5mlow")
    assert [(f, p, t) for f, p, t, _w in ours["legs"]] == ref["legs"] and ref["out"] == "tp1+bell"


# ── 4. META 2026-09-15 from the recorded bars ─────────────────────────

META_0915 = [   # rsl_book_bars, ET, (o, h, l, c) — 9:30 .. 10:10
    ("09:30", 659.3, 663.61, 656.2, 663.39),
    ("09:31", 663.7, 664.52, 661.96, 662.36),
    ("09:32", 662.34, 662.74, 660.285, 661.18),
    ("09:33", 661.48, 664.31, 661.201, 663.73),
    ("09:34", 663.48, 664.653, 662.7535, 664.565),
    ("09:35", 664.565, 667.23, 664.533, 666.2438),
    ("09:36", 666.6463, 667.99, 666.13, 666.97),
    ("09:37", 666.96, 668.5, 666.88, 668.165),
    ("09:38", 668.24, 669.74, 667.0069, 669.27),
    ("09:39", 669.21, 671.36, 669.1201, 671.36),
    ("09:40", 671.46, 673.1, 671.06, 672.54),
    ("09:41", 672.34, 673.05, 671.2, 672.645),
    ("09:42", 672.645, 672.7, 671.34, 672.27),
    ("09:43", 672.385, 672.5, 671.26, 671.4212),
    ("09:44", 671.555, 675.5, 671.38, 675.165),      # the ORB high = the session high
    ("09:45", 675.4, 675.4, 670.16, 670.7399),
    ("09:46", 670.48, 671.97, 669.94, 671.9),        # the GO bar: entry 671.90
    ("09:47", 671.71, 671.88, 670.1201, 671.08),
    ("09:48", 671.08, 672.5, 671.08, 671.83),
    ("09:49", 671.87, 672.32, 670.5, 670.84),
    ("09:50", 670.98, 672.2, 670.48, 671.29),
    ("09:51", 671.57, 671.65, 671.1171, 671.5),
    ("09:52", 671.57, 671.73, 670.08, 670.605),
    ("09:53", 670.71, 670.7546, 669.52, 670.36),
    ("09:54", 670.36, 670.3999, 669.7, 669.775),
    ("09:55", 669.795, 670.39, 669.2901, 669.8675),
    ("09:56", 669.74, 669.99, 669.0, 669.4725),
    ("09:57", 669.4625, 671.36, 669.2401, 671.26),
    ("09:58", 671.36, 672.41, 671.2392, 672.27),
    ("09:59", 672.38, 672.77, 671.8, 672.77),
    ("10:00", 672.645, 675.6, 672.12, 673.9),         # TP1 675.50 touched
    ("10:01", 674.165, 675.75, 673.07, 674.345),
    ("10:02", 674.16, 677.3, 673.23, 676.3),
    ("10:03", 676.2001, 678.87, 676.2001, 678.73),    # the day's high, +3.0R
    ("10:04", 678.67, 678.87, 677.01, 677.87),        # block low 672.12 -> ratchet
    ("10:05", 677.855, 678.16, 673.7706, 673.99),
    ("10:06", 673.915, 674.1, 670.2, 671.3),          # 672.12 touched: runner out
    ("10:07", 671.305, 672.31, 669.17, 671.16),
    ("10:08", 671.4896, 671.4896, 668.0, 668.81),
    ("10:09", 668.56, 668.99, 666.95, 667.2804),      # v1's trail exit, -2.01R
    ("10:10", 667.0646, 667.52, 664.79, 665.45),
]


def _meta():
    out = []
    for hm, o, h, l, c in META_0915:
        hh, mm = hm.split(":")
        out.append((dt.datetime(2026, 9, 15, int(hh), int(mm)), o, h, l, c))
    return out


def test_meta_0915_replay_v1_vs_v2():
    b = _meta()
    i_go = 16                                    # the 9:46 bar
    assert b[i_go][0].time() == dt.time(9, 46) and b[i_go][4] == 671.9
    entry, stop = 671.90, 669.605
    risk = entry - stop
    # v1, as recorded: the trail took it out at the 10:05-10:09 block's close
    v1 = rb.lifecycle_state(b, i_go, entry, stop)
    assert v1["exit"][0] == "trail" and v1["exit"][2] == 667.2804
    assert round((v1["exit"][2] - entry) / risk, 2) == -2.01
    # v2 levels from the recorded inputs: PDH 668.60 and PMH 664.65 sit
    # BELOW entry; the ORB high and the pre-GO session high are one print
    # at 675.50 -> TP1 675.50, no TP2 (the strike grid is not consulted
    # when a structural level is in reach)
    lv = rb.select_levels(b, i_go, entry, pdh=668.6, pmh=664.6488)
    assert lv["tp1"] == {"px": 675.5, "kind": "orh/hod"} and lv["tp2"] is None
    assert lv["fallback"] is False and lv["holes"] == []
    assert lv["levels"]["strike"] == 675.0       # would have been TP1 in the graded naive set
    v2 = rb.lifecycle_state_v2(b, i_go, entry, stop, 675.5, None)
    assert v2["exit"][0] == "tp1_ratchet"
    assert v2["legs"][0] == (0.5, 675.5, b[30][0], "tp1")            # 10:00
    assert v2["legs"][1] == (0.5, 672.12, b[36][0], "runner_stop")   # 10:06
    assert abs(v2["exit"][2] - 673.81) < 1e-9
    assert round((v2["exit"][2] - entry) / risk, 2) == 0.83
    # and at 10:04 (before the runner resolved) the state the ping reads
    mid = rb.lifecycle_state_v2(b[:35], i_go, entry, stop, 675.5, None)
    assert mid["tp1_hit"] and mid["exit"] is None and mid["stop"] == 672.12
    assert mid["stop_mode"] == "touch"
    # legs_json round-trips the record
    js = rb.legs_json(v2["legs"])
    assert js[0]["why"] == "tp1" and js[1]["px"] == 672.12 and js[1]["ts"].startswith("2026-09-15T10:06")


# ── 5. book birth ─────────────────────────────────────────────────────

def test_book_birth_touches_every_place():
    from analysis import ledger_audit, paper_trader
    from alerts import rsleader_ping as rp
    mig = open(os.path.join(ROOT, "migrations", "073_rs_leader_v2.sql")).read()
    for needle in ("'rs_leader_v2'::text", "'skipped_rank'::text", "'tp1_be'::text",
                   "'tp1_ratchet'::text", "'tp1_tp2'::text", "'tp1_eod'::text",
                   "ADD COLUMN IF NOT EXISTS legs jsonb",
                   "ADD COLUMN IF NOT EXISTS levels jsonb"):
        assert needle in mig
    assert ledger_audit.LEGAL_EXITS["rs_leader_v2"] == {
        "disaster", "stop", "eod_flat", "manual", "tp1_be", "tp1_ratchet", "tp1_tp2", "tp1_eod"}
    assert "trail" not in ledger_audit.LEGAL_EXITS["rs_leader_v2"]
    assert {"tp1_be", "tp1_ratchet", "tp1_tp2"} <= ledger_audit.PINGED_EXITS
    assert "tp1_eod" not in ledger_audit.PINGED_EXITS
    rows = [("rs_leader_v2", "META", 671.9, "d", 673.81, "d", "tp1_ratchet", 0.83),
            ("rs_leader_v2", "META", 671.9, "d", 667.28, "d", "trail", -2.01)]
    anomalies, _, _ = ledger_audit.audit(rows, {("META", "d"): (664.0, 679.0)})
    assert len(anomalies) == 1 and "illegal exit_reason 'trail'" in anomalies[0]
    assert "from analysis.rs_leader_book import BOOK as RSL_BOOK" in inspect.getsource(ledger_audit.run)
    assert "NOT IN ('day_bias','rs_leader','rs_leader_v2','two_test')" in inspect.getsource(paper_trader)
    src = inspect.getsource(rb.run_rsl_tick)
    for needle in ("levels=%s::jsonb", "legs=%s::jsonb", "lifecycle_state_v2(",
                   "level_inputs(conn, ticker, today)", "select_levels(bars, i, entry, pdh, pmh)",
                   "describe_levels(lv)"):
        assert needle in src
    assert "lifecycle_state(bars" not in src              # the trail no longer decides the book
    assert rb.SETUP == "rsl_go_levels"
    # the ping reads the book's name, never a literal
    assert "book=%s" in inspect.getsource(rp.run_go_watch)
    # the premarket writer owns the seat's PMH
    from analysis import premarket_backfill
    assert "PLTR" in premarket_backfill.TICKERS


# ── 6. the tick, end to end, on a fake connection ─────────────────────
# (the twotest lesson: two boot cycles were lost to SQL/shape errors a
# fake-connection smoke run catches before shipping)

class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append((" ".join(sql.split()), params))
        self._last = " ".join(sql.split())

    def fetchone(self):
        if "FROM paper_specs WHERE book=" in self._last:
            return self.conn.spec
        if "FROM paper_trades WHERE spec_id=" in self._last:
            return self.conn.trade
        return None


class _Conn:
    def __init__(self, spec, trade):
        self.spec, self.trade, self.log, self.commits = spec, trade, [], 0

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _run_tick(monkeys, spec, trade, now_hm, n_bars):
    import json
    from zoneinfo import ZoneInfo
    from analysis import paper_trader
    et = ZoneInfo("America/New_York")
    conn = _Conn(spec, trade)
    bars = [(t.replace(tzinfo=et), o, h, l, c) for t, o, h, l, c in _meta()[:n_bars]]

    class _Now(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            hh, mm = now_hm.split(":")
            return dt.datetime(2026, 9, 15, int(hh), int(mm), 30, tzinfo=et)

    saved = (paper_trader.get_db_connection, rb._persist_1m, rb.level_inputs, rb.dt)
    try:
        paper_trader.get_db_connection = lambda: conn
        rb._persist_1m = lambda c, tk, day: bars
        rb.level_inputs = lambda c, tk, day: (668.6, 664.6488)
        rb.dt = type("dtmod", (), {"datetime": _Now, "time": dt.time,
                                   "timedelta": dt.timedelta, "timezone": dt.timezone})
        rb.run_rsl_tick()
    finally:
        paper_trader.get_db_connection, rb._persist_1m, rb.level_inputs, rb.dt = saved
    return conn, json


def test_tick_go_fill_partial_and_exit_on_a_fake_connection():
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    # A. armed, no trade, 9:47: the 9:46 GO fills at its close and the
    # levels freeze on the spec (target = TP1, a real price)
    conn, json = _run_tick(None, (824, "META", "armed", 0, None), None, "09:47", 17)
    ins = [p for s, p in conn.log if s.startswith("INSERT INTO paper_trades")]
    upd = [(s, p) for s, p in conn.log if s.startswith("UPDATE paper_specs SET status='triggered'")]
    assert len(ins) == 1 and ins[0][2] == 671.9 and ins[0][1].time() == dt.time(9, 46)
    assert len(upd) == 1
    s, p = upd[0]
    assert "levels=%s::jsonb" in s and "rationale = rationale || %s" in s
    assert p[0] == 671.9 and p[1] == 669.605 and p[2] == 675.5
    assert json.loads(p[3])["tp1"] == {"px": 675.5, "kind": "orh/hod"}
    assert "TP1 675.50 (orh/hod), no TP2" in p[4]
    assert conn.commits == 1
    lv = json.loads(p[3])
    # B. triggered, trade open, 10:03: the TP1 partial is on the record
    trade = (276, dt.datetime(2026, 9, 15, 9, 46, tzinfo=et), 671.9, None, None)
    conn, json = _run_tick(None, (824, "META", "triggered", 669.605, lv), trade, "10:03", 34)
    legs_upd = [(s, p) for s, p in conn.log if s.startswith("UPDATE paper_trades SET legs=")]
    assert len(legs_upd) == 1
    legs = json.loads(legs_upd[0][1][0])
    assert legs == [{"frac": 0.5, "px": 675.5, "ts": "2026-09-15T10:00:00-04:00", "why": "tp1"}]
    assert not any(s.startswith("UPDATE paper_trades SET exited_at=") for s, _ in conn.log)
    # the same tick again with the legs already stored writes nothing
    trade2 = trade[:4] + (legs,)
    conn, _ = _run_tick(None, (824, "META", "triggered", 669.605, lv), trade2, "10:03", 34)
    assert conn.commits == 0
    # C. 10:07: the runner touched 672.12 at 10:06 -> the whole trade closes
    conn, json = _run_tick(None, (824, "META", "triggered", 669.605, lv), trade2, "10:07", 37)
    ex = [(s, p) for s, p in conn.log if s.startswith("UPDATE paper_trades SET exited_at=")]
    assert len(ex) == 1
    s, p = ex[0]
    ts, px, reason, r, legs_js, tid = p
    assert reason == "tp1_ratchet" and px == 673.81 and round(r, 2) == 0.83 and tid == 276
    assert ts.time() == dt.time(10, 6)
    assert [lg["why"] for lg in json.loads(legs_js)] == ["tp1", "runner_stop"]
    # D. no leader clears the bar -> the stand-aside row carries skipped_rank
    # (admitted by migration 073 — it never was before)
    assert "'skipped_rank'" in inspect.getsource(rb.run_rsl_tick)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
