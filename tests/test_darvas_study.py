"""The Darvas Box machine, pinned (2026-09-05): a box opens on a 52-week
high, the top confirms after three failures, the bottom after three,
a WICK through the top is not an entry (close rule), a close below the
bottom invalidates, the trail raises the stop only to a HIGHER completed
box bottom, the touch variant exits at the stop, the max-hold cap fires,
and the module writes only its own tables.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import darvas_study as ds  # noqa: E402

L = ds.LOOKBACK


def _flat(n, px=10.0):
    return [px] * n, [px] * n, [px] * n


def _bars():
    """252 flat bars at 10, then: new high 12 (bar L), three bars under 12
    (top confirms), lows 11.0, 11.2, 11.1 then three bars not under 11.0
    (bottom confirms at 11.0) -> box 11.0/12.0 ready; then a wick to 12.5
    closing 11.8 (no entry); then a close at 12.3 (entry)."""
    h, l, c = _flat(L)
    seq = [  # (high, low, close)
        (12.0, 11.5, 11.9),            # L: new 52w high
        (11.9, 11.4, 11.6), (11.8, 11.0, 11.2), (11.7, 11.2, 11.5),   # top confirms (3 fails), bottom tracking 11.0
        (11.6, 11.1, 11.4), (11.5, 11.05, 11.3), (11.6, 11.2, 11.5),  # 3 bars not under 11.0 -> ready
        (12.5, 11.7, 11.8),            # wick through 12.0, close below: NOT an entry
        (12.4, 12.0, 12.3),            # close > 12.0: ENTRY at 12.3
    ]
    for hh, ll, cc in seq:
        h.append(hh); l.append(ll); c.append(cc)
    return h, l, c


def test_box_and_close_only_entry():
    h, l, c = _bars()
    ok = [True] * len(h)
    boxes = ds.find_boxes(h, l, c, ok)
    assert len(boxes) == 1
    b = boxes[0]
    assert b["top"] == 12.0 and b["bottom"] == 11.0
    assert b["entry_bar"] == len(h) - 1          # the wick bar did not enter
    assert c[b["entry_bar"]] == 12.3


def test_invalidation_and_no_start_without_liquidity():
    h, l, c = _bars()
    h, l, c = h[:-2], l[:-2], c[:-2]              # box ready, then...
    h += [11.2]; l += [10.5]; c += [10.8]         # close below 11.0 -> invalidated
    ok = [True] * len(h)
    boxes = ds.find_boxes(h, l, c, ok)
    assert len(boxes) == 1 and boxes[0]["entry_bar"] is None
    ok2 = [False] * len(h)                        # liquidity gate: no box may start
    assert ds.find_boxes(h, l, c, ok2) == []


def test_exits_trail_touch_and_cap():
    h, l, c = _bars()
    eb = len(h) - 1
    # after entry: run to 14, form a new box 13.0/14.0, then fall through 13
    seq = [(13.0, 12.2, 12.9), (14.0, 13.3, 13.9),
           (13.9, 13.2, 13.5), (13.8, 13.0, 13.4), (13.7, 13.1, 13.3),   # top 14 confirms; bottom tracking 13.0
           (13.6, 13.05, 13.4), (13.5, 13.1, 13.3), (13.6, 13.2, 13.4),  # bottom 13.0 confirms -> stop 13.0
           (13.3, 12.8, 12.7)]                                            # close 12.7 < 13.0 -> trail exit
    for hh, ll, cc in seq:
        h.append(hh); l.append(ll); c.append(cc)
    s = ds.simulate(h, l, c, eb, 12.0, 11.0, "trail_close")
    assert s["reason"] == "trail_stop" and s["stop_final"] == 13.0 and s["exit_px"] == 12.7
    s2 = ds.simulate(h, l, c, eb, 12.0, 11.0, "trail_touch")
    assert s2["reason"] == "trail_stop" and s2["exit_px"] == 13.0      # touched 12.8 <= 13.0 -> out AT the stop
    s3 = ds.simulate(h, l, c, eb, 12.0, 11.0, "box_close")
    assert s3["reason"] == "record_end" and s3["stop_final"] == 11.0    # never trailed, never hit -> hole
    # max-hold cap
    h2, l2, c2 = _bars()
    for _ in range(ds.MAX_HOLD + 5):
        h2.append(13.0); l2.append(12.5); c2.append(12.8)
    s4 = ds.simulate(h2, l2, c2, eb, 12.0, 11.0, "box_close")
    assert s4["reason"] == "max_hold" and s4["exit_bar"] - eb == ds.MAX_HOLD


def test_scope():
    src = inspect.getsource(ds)
    assert "INSERT INTO darvas_events" in src and "darvas_progress" in src
    assert "FROM daily_prices_clean" in src                 # reads the anomaly-safe view
    for forbidden in ("INSERT INTO paper_", "UPDATE daily_prices", "INSERT INTO daily_prices", "trade_journal"):
        assert forbidden not in src
    assert ds.VARIANTS == ("trail_close", "trail_touch", "box_close")
    sched = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "alerts", "scheduler.py")).read()
    assert "analysis.darvas_study" in sched and 'id="darvas_study"' in sched


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
