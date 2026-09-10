"""The range-edge failure test (2026-09-10), pinned:
  1. An event needs a CLOSE beyond the edge and the NEXT close back
     inside; a wick beyond is not a probe; a second close beyond is
     acceptance and the edge yields no event that day; ORB probes
     cannot start before the range exists.
  2. Entry is the failing bar's close against the break; stop is the
     probe's extreme; target is the opposite edge of the same range.
  3. simulate: the close-rule stop survives a touch and fires on a
     close; the touch rule fires on the touch; the target fills on a
     touch; same-bar target-and-stop is a stop; otherwise eod.
  4. Writes only its own tables.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import failtest_study as ft  # noqa: E402


def _bars(specs, d=dt.date(2026, 9, 9)):
    t0 = dt.datetime(d.year, d.month, d.day, 9, 30)
    return [(t0 + dt.timedelta(minutes=15 * i), *s) for i, s in enumerate(specs)]


def test_event_definition():
    pdh, pdl = 105.0, 95.0
    # bars: (o, h, l, c). ORB = bars 0-1 → ORBH 102, ORBL 99.
    base = [(100, 102, 99, 101), (101, 101.5, 99.5, 100)]
    # bar 2 wicks over PDH but closes inside → no probe; bar 3 CLOSES over; bar 4 closes back inside
    specs = base + [(100, 105.5, 99.8, 104), (104, 106, 103.5, 105.4), (105.4, 105.8, 103, 103.9)]
    specs += [(103.9, 104, 101, 101.5)] * 20
    evs = ft.find_events(_bars(specs), pdh, pdl)
    fam = {e["family"]: e for e in evs}
    assert "pdh" in fam and fam["pdh"]["direction"] == "short"
    assert fam["pdh"]["i_probe"] == 3 and fam["pdh"]["i_entry"] == 4
    assert fam["pdh"]["entry"] == 103.9 and fam["pdh"]["stop"] == 106 and fam["pdh"]["target"] == pdl
    # ORBH (102) was first CLOSED beyond at bar 2 (104) and bar 3 closed beyond again → accepted, no event
    assert "orbh" not in fam
    # nothing probed the low side
    assert "pdl" not in fam and "orbl" not in fam
    # ORB probes cannot start inside the range window: a bar-1 close above ORBH is impossible by construction
    assert ft.edges(_bars(specs), pdh, pdl)["orbh"][2] == 2


def test_simulate_rules():
    ev = {"direction": "short", "entry": 103.9, "stop": 106.0, "target": 95.0, "i_entry": 0}
    # a wick to 106.5 that closes 105 does not stop the close rule; it does stop the touch rule
    bars = _bars([(103.9, 104, 103, 103.9), (104, 106.5, 103.5, 105), (105, 105.2, 100, 100.5)] + [(100, 101, 99, 100)] * 5)
    sc = ft.simulate(bars, ev, "close")
    st = ft.simulate(bars, ev, "touch")
    assert sc["outcome"] == "eod" and st["outcome"] == "stop" and st["exit_px"] == 106.0
    # a close through the stop fires the close rule at the CLOSE
    bars = _bars([(103.9, 104, 103, 103.9), (104, 106.5, 103.5, 106.2)] + [(106, 107, 105, 106)] * 5)
    sc = ft.simulate(bars, ev, "close")
    assert sc["outcome"] == "stop" and sc["exit_px"] == 106.2 and sc["r"] < 0
    # the target fills on a touch
    bars = _bars([(103.9, 104, 103, 103.9), (103, 103.5, 94.8, 96)] + [(96, 97, 95.5, 96)] * 5)
    sc = ft.simulate(bars, ev, "close")
    assert sc["outcome"] == "target" and sc["exit_px"] == 95.0 and abs(sc["r"] - (103.9 - 95.0) / 2.1) < 1e-3
    # same bar touches the target AND closes through the stop → stop (conservative)
    bars = _bars([(103.9, 104, 103, 103.9), (103, 106.5, 94.8, 106.3)] + [(106, 107, 105, 106)] * 5)
    assert ft.simulate(bars, ev, "close")["outcome"] == "stop"


def test_writes_own_tables_and_bar_stated():
    src = inspect.getsource(ft)
    assert "INSERT INTO failtest_events" in src and "INSERT INTO failtest_days" in src
    for forbidden in ("INSERT INTO paper_", "UPDATE ", "DELETE FROM", "INSERT INTO daytype"):
        assert forbidden not in src, forbidden
    assert "both eras AND on\n             both tickers" in src or "both eras AND on both tickers" in src.replace("\n             ", " ")
    assert "LEAST(GREATEST(" in ft.READOUT_SQL


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
