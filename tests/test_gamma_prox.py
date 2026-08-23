"""Gamma proximity alerts, pinned (2026-08-23).

  1. The band: spot within 0.25% of a wall/flip is a hit; outside is
     not; null levels are skipped, never invented.
  2. One ping per level per day: the claim ref quantizes to cents, so
     a cent wobble is the SAME level (no re-ping) and a wall walking
     to a new strike is a NEW one (may ping again).
  3. The message carries the reading rules: decoration magnitude says
     so, and inverted walls (put wall above / call wall below spot)
     are labeled — the NVDA lesson.
  4. Dust boards (|GEX| < MIN_GEX_BN) never alert — never present
     decoration walls as levels.
"""
import datetime as dt
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts.gamma_prox import (DECOR_BN, MIN_GEX_BN, format_prox,  # noqa: E402
                               near_levels, prox_ref)


def test_band_hits_and_misses():
    # spot 500.0; cw 501 (0.2% away -> hit), pw 490 (2% -> miss),
    # flip 499.5 (0.1% -> hit)
    hits = near_levels(500.0, 501.0, 490.0, 499.5)
    kinds = {h["kind"] for h in hits}
    assert kinds == {"call_wall", "gamma_flip"}
    cw = next(h for h in hits if h["kind"] == "call_wall")
    assert cw["dist_pct"] == -0.2      # spot BELOW the wall, signed
    assert near_levels(500.0, None, None, None) == []
    assert near_levels(0, 501.0, 490.0, 499.5) == []


def test_ref_wobble_same_walk_new():
    d = dt.date(2026, 8, 24)
    a = prox_ref(d, "QQQ", "call_wall", 730.00)
    assert a == prox_ref(d, "QQQ", "call_wall", 730.001)   # cent wobble
    assert a != prox_ref(d, "QQQ", "call_wall", 700.00)    # wall walk
    assert a != prox_ref(dt.date(2026, 8, 25), "QQQ", "call_wall", 730.0)


def test_message_labels_inversion_and_decoration():
    hit = {"kind": "put_wall", "label": "PUT WALL", "level": 505.0,
           "dist_pct": -0.4}
    m = format_prox("NVDA", "14:15", 503.0, hit, 0.21, "slippery")
    assert "INVERTED" in m and "not support" in m
    assert "decoration magnitude" in m          # 0.21 < DECOR_BN
    assert 0.21 < DECOR_BN
    hit2 = {"kind": "call_wall", "label": "CALL WALL", "level": 640.0,
            "dist_pct": 0.1}
    m2 = format_prox("SPY", "10:30", 640.6, hit2, 1.8, "pinning")
    assert "INVERTED" in m2 and "stabilizer" in m2   # cw below spot
    assert "decoration" not in m2                    # 1.8bn is load-bearing


def test_dust_boards_never_alert_by_source():
    from alerts import gamma_prox
    src = inspect.getsource(gamma_prox.run_gamma_prox_check)
    assert "MIN_GEX_BN" in src and "continue" in src
    # At-most-once rides the shared claim log, and nothing else writes.
    assert "claim_and_send" in src
    full = inspect.getsource(gamma_prox)
    for forbidden in ("INSERT INTO paper", "UPDATE paper",
                      "INSERT INTO gex", "UPDATE gex"):
        assert forbidden not in full, forbidden
    assert MIN_GEX_BN < DECOR_BN


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
