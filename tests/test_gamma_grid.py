"""The strike x expiry grid + wall strength (2026-09-02, the SPXVIX-card
comparison), pinned on a synthetic chain:

  1. compute_gex still finds the same walls/flip/regime and now reports
     each wall's weight, its SHARE of its side, and the next-strongest
     strike.
  2. by_expiry is bounded (strike window, DTE window, cell floor) and
     nets calls minus puts per (expiry, strike).
  3. persist_strike_grid writes exactly the grid cells (stub cursor).
  4. The board rows carry wall_strength beside the walls (source pin).
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import gex  # noqa: E402


def _chain(spot=100.0):
    near = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    far = (dt.date.today() + dt.timedelta(days=45)).isoformat()
    too_far = (dt.date.today() + dt.timedelta(days=100)).isoformat()
    rows = []

    def c(strike, is_call, oi, exp, gamma=2.0):
        rows.append({"strike": strike, "exp_days": 7, "exp": exp,
                     "iv": 0.25, "oi": oi, "gamma": gamma,
                     "is_call": is_call})
    # calls: 105 heaviest (near + far), 110 lighter
    c(105.0, True, 4000, near); c(105.0, True, 2000, far); c(110.0, True, 1500, near)
    # puts: 95 heaviest, 90 lighter, 92 tiny
    c(95.0, False, 5000, near); c(90.0, False, 1500, far); c(92.0, False, 50, near)
    # outside the grid window: 130 strike, and a 100-DTE expiry
    c(130.0, True, 500, near); c(100.0, True, 500, too_far)
    # pad to clear MIN_CONTRACTS / MIN_TOTAL_OI with dust
    for i in range(60):
        c(100.0 + (i % 5), i % 2 == 0, 60, far, gamma=0.0001)
    return spot, rows


def test_walls_strength_and_grid(monkeypatch=None):
    orig = gex._fetch_gex_chain
    gex._fetch_gex_chain = lambda t: _chain()
    try:
        g = gex.compute_gex("TEST")
    finally:
        gex._fetch_gex_chain = orig
    assert g and g["call_wall"] == 105.0 and g["put_wall"] == 95.0
    cs, ps = g["call_wall_strength"], g["put_wall_strength"]
    assert cs["next_strike"] == 110.0 and ps["next_strike"] == 90.0
    assert 0.5 < cs["share"] <= 1.0 and 0.5 < ps["share"] <= 1.0
    assert cs["gex_bn"] > cs["next_bn"] > 0
    cells = {(x["expiry"], x["strike"]): x for x in g["by_expiry"]}
    assert all(abs(k / 100.0 - 1) <= gex.GRID_PCT for _, k in cells)   # window
    assert not any(k == 130.0 for _, k in cells)                        # outside
    assert not any(e > (dt.date.today() + dt.timedelta(days=gex.GRID_DTE)).isoformat()
                   for e, _ in cells)                                    # DTE
    near = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    put95 = cells[(near, 95.0)]
    assert put95["gex_bn"] < 0 and put95["put_bn"] > 0 and put95["call_bn"] == 0
    assert (near, 92.0) not in cells                                     # below floor


def test_persist_writes_grid_cells_only():
    calls = []

    class Cur:
        def executemany(self, sql, rows):
            calls.append((sql, list(rows)))
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class Conn:
        def cursor(self): return Cur()
    g = {"by_expiry": [{"expiry": "2026-09-10", "strike": 95.0, "gex_bn": -0.5,
                        "call_bn": 0.0, "put_bn": 0.5}]}
    n = gex.persist_strike_grid(Conn(), "TEST", g, dt.datetime(2026, 9, 2, tzinfo=dt.timezone.utc))
    assert n == 1 and "gex_strike_expiry" in calls[0][0]
    assert calls[0][1][0][3] == 95.0
    assert gex.persist_strike_grid(Conn(), "TEST", {"by_expiry": []}) == 0


def test_board_rows_carry_wall_strength():
    src = inspect.getsource(gex)
    assert src.count("wall_strength=EXCLUDED.wall_strength") == 2   # both upserts
    assert "regime, wall_strength)" in src                          # intraday insert
    assert "def _strength_blob" in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
