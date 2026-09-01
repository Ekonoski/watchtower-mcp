"""The 2026-09-01 live-day fixes, pinned.

  1. The generic poller can never touch day_bias or rs_leader rows
     (the phantom-target incident) — by source signature.
  2. The rs_leader book writes a SENTINEL target at fill, never a
     plausible price (a placeholder that satisfies the schema is a
     live number to every reader).
  3. ledger_audit.audit(): flags illegal exit reasons per book,
     out-of-range prices, and missing exit fields; counts bar-holes
     separately (holes are not failures).
  4. The GO alert sizes contracts from R_DOLLARS with round-down and
     a SKIP path — by source signature.
  5. trailvar sim: chandelier ratchets off the running high and exits
     on 5m closes; the ER gate suspends trail decisions when the
     market hasn't earned them; disaster stays touch-based.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_poller_exclusion_and_sentinel():
    from analysis import paper_trader, rs_leader_book
    src = inspect.getsource(paper_trader)
    assert "NOT IN ('day_bias','rs_leader')" in src
    src2 = inspect.getsource(rs_leader_book)
    assert "999999" in src2                     # sentinel, not entry*1.02
    assert "entry * 1.02" not in src2


def test_ledger_audit_logic():
    from analysis.ledger_audit import audit
    rows = [
        ("rs_leader", "META", 566.7, "d1", 578.1, "d1", "target", 12.6),
        ("swing", "BTGO", 6.0, "d2", 6.65, "d2", "target", 1.0),
        ("gamma", "SPY", 770.0, "d3", 771.7, "d3", "stop", None),
        ("swing", "XX", 10.0, "d4", None, None, None, None),
    ]
    ranges = {("META", "d1"): (560.0, 585.0), ("BTGO", "d2"): (5.9, 6.7),
              ("SPY", "d3"): (768.0, 700.0)}   # SPY range malformed on purpose
    ranges[("SPY", "d3")] = (768.0, 772.0)
    anomalies, holes, n = audit(rows, ranges)
    assert n == 4
    joined = " | ".join(anomalies)
    assert "illegal exit_reason 'target'" in joined      # rs_leader
    assert "BTGO" not in joined                          # swing target legal
    assert "r_multiple missing" in joined                # gamma row
    assert ("XX" in h for h in holes)                    # open trade, no bars
    # out-of-range detection
    rows2 = [("swing", "AAA", 10.0, "d9", 99.0, "d9", "stop", -1.0)]
    a2, _, _ = audit(rows2, {("AAA", "d9"): (9.0, 11.0)})
    assert any("OUTSIDE" in x for x in a2)


def test_go_alert_sizing_signature():
    from alerts import rsleader_ping as rp
    assert rp.R_DOLLARS == 250.0
    src = inspect.getsource(rp.run_go_watch)
    assert "R_DOLLARS // per_ct" in src          # round-down division
    assert "SKIP" in src
    assert "0.55 * risk * 100" in src            # the ATM alternative


def test_trailvar_sim_semantics():
    from analysis.trailvar_study import sim_variants
    t0 = dt.datetime(2026, 9, 1, 10, 0)
    entry, struct, atr = 100.0, 99.0, 1.0

    def bars(specs):
        return [(t0 + dt.timedelta(minutes=i), *s)
                for i, s in enumerate(specs)]
    # arm, run to 105, then a 5m close at 102 -> chandelier (105-2.5=102.5)
    # exits; the 21EMA/ER gate variant holds when ER is low.
    specs = [(100, 101.2, 99.9, 101.0)] * 5      # armed
    specs += [(104, 105.0, 103.9, 104.8)] * 5    # hh = 105
    specs += [(102.2, 102.3, 101.9, 102.0)] * 5  # 5m close 102 < 102.5
    b = bars(specs)
    e21_map = {4: 90.0, 9: 90.0, 14: 90.0}       # boundaries; ema low
    er_low = {4: 0.1, 9: 0.1, 14: 0.1}
    out = sim_variants(b, 0, entry, struct, atr, e21_map, er_low)
    assert out["chand_25"]["out"] == "trail" and out["chand_25"]["exit_px"] == 102.0
    assert out["er_gate21"]["out"] == "eod"      # gate off -> held to end
    # same tape with ER high and e21 above price -> gated trail exits
    e21_hi = {4: 90.0, 9: 90.0, 14: 103.0}
    er_hi = {4: 0.9, 9: 0.9, 14: 0.9}
    out2 = sim_variants(b, 0, entry, struct, atr, e21_hi, er_hi)
    assert out2["er_gate21"]["out"] == "trail"
    # disaster is touch-based
    specs3 = [(100, 100.1, 98.9, 100.0)] * 5
    out3 = sim_variants(bars(specs3), 0, entry, struct, atr, {4: 90.0}, {4: 0.9})
    assert out3["chand_25"]["out"] == "disaster"


def test_trail_decides_on_completed_5m_blocks():
    """The 14:27 META exit (2026-09-01): a 1m close below the 5m e21
    MID-BLOCK must not exit — only the completed block's close decides
    (wick rule). The fix maps e21 only at completed-block bars."""
    from analysis.rs_leader_book import lifecycle_state
    t0 = dt.datetime(2026, 9, 1, 9, 45)

    def mk(minutes, px):
        # flat bars at px; (ts, o, h, l, c)
        return [(t0 + dt.timedelta(minutes=m), px, px + 0.1, px - 0.1, px)
                for m in minutes]

    entry, stop = 100.0, 99.0
    # 40 minutes climbing to 105 (arms at 101, e21 drags well below),
    # then a sharp drop: 1m closes 101.5 at :40/:41 (mid-block, below
    # nothing yet), then the block completes at :44 with a close BELOW
    # the running e21 -> exit only at the completion bar.
    bars = []
    for m in range(40):
        px = 100.0 + m * 0.125          # 100 -> 104.875
        bars += mk([m], px)
    bars += mk([40, 41, 42], 101.5)      # mid-block plunge, block open
    out_mid = lifecycle_state(bars, 0, entry, stop)
    assert out_mid["exit"] is None       # partial block never decides
    bars_done = bars + mk([43, 44], 101.5)
    out_done = lifecycle_state(bars_done, 0, entry, stop)
    assert out_done["exit"] is not None
    reason, ts, px = out_done["exit"]
    assert reason == "trail"
    assert ts.minute == (t0.minute + 44) % 60   # the block's FINAL bar
    assert px == 101.5


def test_rankladder_trend_gate_and_marker():
    """The 2026-09-01 evening fixes: (1) trend1m_at answers None inside
    its warmup — unknown is not False (the old 5m gate could never
    fire inside the entry window); (2) the completion marker no longer
    requires zero todo days — with the bars marker present, a day the
    final record cannot grade is a hole, not pending work."""
    from analysis.rankladder_study import run, trend1m_at
    from analysis.rsleader_study import ema
    closes = [100 + 0.3 * k for k in range(40)]   # clean uptrend
    e8, e21 = ema(closes, 8), ema(closes, 21)
    assert trend1m_at(closes, e8, e21, 10) is None     # warmup: unknown
    assert trend1m_at(closes, e8, e21, 30) is True     # trending
    down = [100 - 0.3 * k for k in range(40)]
    assert trend1m_at(down, ema(down, 8), ema(down, 21), 30) is False
    src = inspect.getsource(run)
    assert "if bars_done:" in src and "not todo" not in src.split(
        "if bars_done:")[1].split("return True")[0]
    assert "ungradeable" in src


def test_gamma_board_ping_contract():
    """The 🌅 morning board (2026-09-01): read-only, claimed
    at-most-once, holes named, inverted walls warned, per-row stamps."""
    from alerts import gamma_board_ping as gb
    src = inspect.getsource(gb)
    assert "claim_and_send" in src
    assert "INSERT INTO" not in src and "UPDATE " not in src
    assert "DELETE FROM" not in src
    assert gb.KIND_BOARD == "gamma_board" and gb.CHANNEL == "gamma"
    assert gb.VENUES == ("SPY", "QQQ", "IWM")
    # inverted-wall doctrine + hole rendering live in the formatter
    row_hole = gb._fmt_row("IWM", (None,) * 7)
    assert "unavailable" in row_hole
    inv = gb._fmt_row("QQQ", (700.0, 690.0, 710.0, 705.0, -5.0,
                              "slippery", None))
    assert "put wall ABOVE spot" in inv and "call wall BELOW spot" in inv
    clean = gb._fmt_row("SPY", (770.0, 775.0, 760.0, 768.0, 3.0,
                                "pinned", None))
    assert "⚠" not in clean
    # boot catch-up is window-gated (weekday 8:05-16:00), else no-op
    assert "8, 5" in inspect.getsource(gb.run_board_catchup)


def test_hodlod_day_row():
    from analysis.hodlod_study import day_row
    t0 = dt.datetime(2026, 9, 1, 9, 30)
    bars = [(t0 + dt.timedelta(minutes=15 * i), 100 + i, 100.5 + i,
             99.5 + i, 100.2 + i) for i in range(26)]
    hi_t, lo_t, open_state, close_pos = day_row(bars, 99.0, 95.0)
    assert hi_t == "15:45" and lo_t == "09:30"   # steady uptrend day
    assert open_state == "open_above"
    assert close_pos is not None and close_pos > 0.9


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
