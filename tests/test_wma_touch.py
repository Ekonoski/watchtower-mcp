"""The goat-line detector, pinned the day it was built (2026-08-10).

wma_touch formalizes the goat study: a 40-week-qualified uptrend meeting
its 200-week SMA. These tests pin the contract the 7:40 spec-writer
depends on — a synthetic qualified approach must emit an armable
'breakout' row with trigger = the 200w SMA of COMPLETED weeks, invalid
3% under it, target 10% over it — and the refusals: shallow history,
daily timeframe, a broken qualifier, a close through the invalid, and a
line too far below to list.

Standalone per house convention:  python3 tests/test_wma_touch.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.pattern_scan import _ctx, _det_wma_touch  # noqa: E402


def _bars(closes):
    d = dt.date(2016, 1, 4)
    out = []
    for c in closes:
        out.append({"date": d, "high": c * 1.01, "low": c * 0.99,
                    "close": c, "volume": 1_000_000.0})
        d += dt.timedelta(days=7)
    return out


def _qualified_approach():
    """260 rising weeks (every close above the lagging 200w SMA), then a
    controlled fade toward the line until the live bar sits inside the
    0-4%% armable band. The fade never closes below the prior-week SMA,
    so the 40-week qualifier survives it."""
    closes = [100.0 * (1.003 ** i) for i in range(260)]
    while True:
        sma = sum(closes[-200:]) / 200.0
        nxt = max(sma * 1.02, closes[-1] * 0.968)
        closes.append(nxt)
        if nxt / sma <= 1.035:
            closes.append(nxt)            # the live partial week
            return closes


def main():
    closes = _qualified_approach()
    ctx = _ctx(_bars(closes), "weekly")
    r = _det_wma_touch(ctx)
    assert r, "qualified approach inside the band must emit a row"
    assert r["pattern"] == "wma_touch" and r["direction"] == "bullish"
    assert r["status"] == "breakout", f"armable band must be breakout, got {r['status']}"
    sma = sum(closes[:-1][-200:]) / 200.0
    assert abs(r["trigger_price"] - sma) / sma < 1e-6, "trigger must be the 200w SMA"
    assert abs(r["invalid_level"] - sma * 0.97) / sma < 1e-6
    assert abs(r["target"] - sma * 1.10) / sma < 1e-6
    assert 0.0 <= r["dist_to_trigger_pct"] <= 4.0
    assert r["score"] >= 70, f"armable row must clear the writer's 70 gate, got {r['score']}"
    assert r["points"]["up_weeks"] >= 40

    # Daily timeframe: never fires, whatever the data.
    assert _det_wma_touch({**ctx, "tf": "daily"}) is None

    # Shallow history (the regular 3y weekly map): never fires.
    shallow = _ctx(_bars(closes[-160:]), "weekly")
    assert shallow and _det_wma_touch(shallow) is None

    # Broken qualifier: one recent completed week closing under its
    # prior-week SMA resets the run — no row.
    broken = list(closes)
    sma_b = sum(broken[:-1][-200:]) / 200.0
    broken[-5] = sma_b * 0.95
    assert _det_wma_touch(_ctx(_bars(broken), "weekly")) is None

    # Closed through the invalid: dead, not a listing.
    dead = list(closes)
    dead[-1] = sum(dead[:-1][-200:]) / 200.0 * 0.96
    assert _det_wma_touch(_ctx(_bars(dead), "weekly")) is None

    # Line too far below (fresh strong trend, no approach): not listable.
    far = [100.0 * (1.004 ** i) for i in range(300)]
    assert _det_wma_touch(_ctx(_bars(far), "weekly")) is None

    # Low-volatility instrument (a bond-ETF drift that lives ON its 200w
    # line): qualified and inside the band, but the trailing high never
    # clears the line by 15% — excluded, a touch there is noise.
    lowvol = [100.0 * (1.0003 ** i) for i in range(300)]
    lv_ctx = _ctx(_bars(lowvol), "weekly")
    assert _det_wma_touch(lv_ctx) is None

    # Determinism.
    r2 = _det_wma_touch(_ctx(_bars(closes), "weekly"))
    assert r2 == r
    print(f"ok — armable row at trigger {r['trigger_price']}, "
          f"{r['points']['up_weeks']} qualified weeks; all refusals hold")


if __name__ == "__main__":
    main()
