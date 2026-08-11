"""The swing book's cipher tag — measurement that can never become a gate
by accident, and never renders a hole as a reading.

What this pins (decided 2026-08-11, the night the cipher-at-episodes study
read out): every swing spec is stamped with `osc_state` — the cipher
components at its timeframe's last completed bar — so the live ledger can
grade the study's +0.69R-vs-+0.10R weekly split on the desk's own resolved
trades. Three properties matter enough to assert:

1. THE TAG IS HONEST ABOUT TIME. A weekly tag at a premarket write must
   come from the last COMPLETED week (drop_partial), and the tag stamps
   its own as-of date — freshness per row, not per page.
2. A MISSING TAG IS A HOLE, NOT A VERDICT. Short history, a compute
   error, a missing component: all must surface as cipher_ok=None with a
   reason, never as False (False means "the cipher looked and said no";
   None means "nobody looked"). The _social_block lesson, applied before
   the bug instead of after.
3. THE GATE PIPELINE DOES NOT SEE IT. curate_swing and swing_geometry_ok
   take the same inputs they took before the tag existed — asserted here
   by signature, so wiring the tag into arming can't happen without
   breaking this test on purpose. A tiebreaker is a gate in disguise.

Standalone per house convention:  python3 tests/test_cipher_tag.py
"""
import datetime as dt
import inspect
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.paper_trader import (  # noqa: E402
    CIPHER_WT_OVERBOUGHT, cipher_ok, cipher_tag_label, curate_swing,
    swing_geometry_ok, swing_osc_state)


def _series(n=1300, seed=11):
    """Deterministic OHLCV rows, same generator family as
    tests/test_cipher_study.py — the tag rides the study's own compute
    path, so it is tested on the study's own kind of series."""
    rng = random.Random(seed)
    rows, d, px = [], dt.date(2020, 1, 6), 50.0
    i = 0
    while len(rows) < n:
        if d.weekday() < 5:
            px = max(1.0, px * (1 + math.sin(i / 90) * 0.004
                                + rng.uniform(-0.02, 0.02)))
            o = px * (1 + rng.uniform(-0.008, 0.008))
            h = max(o, px) * (1 + rng.uniform(0, 0.012))
            lo = min(o, px) * (1 - rng.uniform(0, 0.012))
            v = float(rng.choice([200000, 800000, 3000000]))
            rows.append((d, round(o, 4), round(h, 4), round(lo, 4),
                         round(px, 4), v))
            i += 1
        d += dt.timedelta(days=1)
    return rows


TAG_KEYS = {"asof", "timeframe", "rsi", "wt2", "mf", "mf_slope_pos",
            "macd_hist_pos", "confluence", "cipher_ok"}


def main():
    # ── cipher_ok: pure truth table, including the missing-component rule ──
    assert cipher_ok(True, True, 10.0) is True
    assert cipher_ok(True, True, CIPHER_WT_OVERBOUGHT - 0.01) is True
    assert cipher_ok(True, True, CIPHER_WT_OVERBOUGHT) is False   # at the band = overbought
    assert cipher_ok(False, True, 10.0) is False
    assert cipher_ok(True, False, 10.0) is False
    assert cipher_ok(None, True, 10.0) is None                    # hole, not verdict
    assert cipher_ok(True, None, 10.0) is None
    assert cipher_ok(True, True, None) is None
    assert cipher_tag_label({"cipher_ok": True}) == "cipher_ok"
    assert cipher_tag_label({"cipher_ok": False}) == "cipher_not"
    assert cipher_tag_label({"cipher_ok": None}) == "unavailable"
    assert cipher_tag_label({}) == "unavailable"
    assert cipher_tag_label(None) == "unavailable"

    bars = _series()

    # ── Daily tag: full contract, as-of the last bar on record ──
    tag = swing_osc_state(bars, "daily")
    assert set(tag) == TAG_KEYS, f"tag contract drifted: {set(tag)}"
    assert tag["asof"] == bars[-1][0].isoformat(), (
        "daily tag must stamp the last recorded bar as its as-of date")
    assert tag["timeframe"] == "daily"
    # The stored boolean must equal the rule applied to the stored
    # components — the tag may never disagree with its own internals.
    assert tag["cipher_ok"] == cipher_ok(
        tag["mf_slope_pos"], tag["macd_hist_pos"], tag["wt2"])
    json.dumps(tag)   # the INSERT stores jsonb; the tag must serialize

    # ── Weekly tag: completed weeks only ──
    wtag = swing_osc_state(bars, "weekly")
    assert set(wtag) == TAG_KEYS
    assert wtag["timeframe"] == "weekly"
    asof = dt.date.fromisoformat(wtag["asof"])
    assert asof <= bars[-1][0], "weekly as-of cannot postdate the data"
    # drop_partial guards the CURRENT calendar week (by design it never
    # repaints history — the NU weekend fix). So test the production case
    # exactly: a series of bars running through today, tagged premarket.
    today = dt.date.today()
    dates, d = [], today
    while len(dates) < len(bars):
        if d.weekday() < 5:
            dates.append(d)
        d -= dt.timedelta(days=1)
    dates.reverse()
    live = [(dates[i],) + bars[i][1:] for i in range(len(bars))]
    live_tag = swing_osc_state(live, "weekly")
    live_asof = dt.date.fromisoformat(live_tag["asof"])
    if today.weekday() < 5:
        assert live_asof.isocalendar()[:2] < today.isocalendar()[:2], (
            "the tag read the CURRENT, still-trading week — weekly signals "
            "must come from the last completed week at a premarket write")
    else:
        # Sat/Sun: the week that closed Friday IS complete and must be used.
        assert live_asof.isocalendar()[:2] == today.isocalendar()[:2]

    # ── Holes are holes ──
    short = swing_osc_state(bars[:50], "daily")
    assert short["cipher_ok"] is None and "unavailable" in short
    assert "50" in short["unavailable"], "the reason must name the shortfall"
    garbage = swing_osc_state([("not", "a", "bar")], "daily")
    assert garbage["cipher_ok"] is None and "unavailable" in garbage
    empty = swing_osc_state([], "weekly")
    assert empty["cipher_ok"] is None and "unavailable" in empty

    # ── The arming pipeline cannot see the tag ──
    # If someone threads osc_state into curation or geometry, these
    # signatures change and this fails loudly. That is the point.
    assert list(inspect.signature(curate_swing).parameters) == ["rows", "cap"]
    assert list(inspect.signature(swing_geometry_ok).parameters) == [
        "pattern", "trigger", "target", "invalid"]

    print(f"ok — tag contract stable, holes render as holes, arming is "
          f"blind to the tag (daily: {cipher_tag_label(tag)}, "
          f"weekly: {cipher_tag_label(wtag)})")


if __name__ == "__main__":
    main()
