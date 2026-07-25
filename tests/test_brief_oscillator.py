"""
Regression tests for the brief's oscillator + day-change rendering.

Both bugs these cover shipped wrong answers to a live trading session on
2026-07-24 ($NOK), so they are worth pinning:

1. The brief flattened the signals JSON to its keys, so "mf_curl down
   (vol-backed)" rendered as "mf_curl" — a bearish flag printed next to the
   word "bullish" and read as agreement. On the 4h it was worse: a 2-of-4
   BEARISH divergence rendered as the bare word "divergence" beside a
   "bullish" label.
2. The day move came from a vendor todaysChangePerc keyed to a different
   previous close than the one in our own bars: "-7.6% today" printed next
   to $9.10 when $9.10 against the $9.73 prior close is -6.5%.

Run: python3 tests/test_brief_oscillator.py   (or pytest)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.brief import (  # noqa: E402
    _osc_conflicts, _osc_dir, _osc_signal_tags, format_brief,
)

# Real oscillator_scan rows for NOK, 2026-07-25.
DAILY = {"timeframe": "daily", "direction": "bullish", "confluence": 33.0,
         "macd_hist": -0.1805, "bar_ts": "2026-07-23 00:00:00+00",
         "signals": {"mf_curl": {"dir": "down", "volume_backed": True},
                     "mf_round": {"dir": "up"},
                     "divergence": {"dir": "bullish", "count": 1,
                                    "indicators": ["macd"]}}}
FOUR_H = {"timeframe": "4h", "direction": "bullish", "confluence": 13.0,
          "macd_hist": -0.0632, "bar_ts": "2026-07-24 12:00:00+00",
          "signals": {"divergence": {"dir": "bearish", "count": 2,
                                     "indicators": ["rsi", "macd"]}}}
WEEKLY = {"timeframe": "weekly", "direction": "bearish", "confluence": 30.0,
          "macd_hist": -0.3884, "bar_ts": "2026-07-17 00:00:00+00",
          "signals": {"mf_round": {"dir": "down"},
                      "divergence": {"dir": "bearish", "count": 1,
                                     "indicators": ["rsi"]}}}

PRICE = {"as_of": "2026-07-23", "close": 9.73, "ret_1d": -5.35,
         "ret_1w": -6.3, "ret_1m": -29.0, "ret_3m": -1.3, "ret_6m": 51.1,
         "hi_52w": 17.45, "lo_52w": 4.00,
         "off_high_pct": -44.2, "off_low_pct": 143.2}


def test_signal_direction_survives_rendering():
    tags = _osc_signal_tags(DAILY["signals"])
    assert "mf_curl down (vol-backed)" in tags, tags
    assert "mf_curl" not in tags, "bare name loses the direction"
    assert "divergence bullish (1/4: macd)" in tags, tags


def test_bare_and_malformed_signal_values_do_not_crash():
    assert _osc_signal_tags({"coil": True, "pctr_hook": {"dir": "up"}}) == [
        "coil", "pctr_hook up"]
    assert _osc_signal_tags({}) == []
    assert _osc_signal_tags(None) == []


def test_direction_vocabularies_normalise():
    # waves/flow say up|down; divergence says bullish|bearish
    assert _osc_dir("bullish") == _osc_dir("up") == "up"
    assert _osc_dir("bearish") == _osc_dir("down") == "down"
    assert _osc_dir("sideways") is None and _osc_dir(None) is None


def test_conflicts_flag_label_vs_internals():
    # bullish label, MACD down and flow curling down
    c = _osc_conflicts(DAILY)
    assert any("MACD confirming down" in x for x in c), c
    assert any("mf_curl down" in x for x in c), c
    # bullish label whose ONLY signal is a bearish divergence
    assert any("divergence bearish" in x for x in _osc_conflicts(FOUR_H))


def test_no_false_positive_when_internals_agree():
    assert _osc_conflicts(WEEKLY) == [], "weekly is internally consistent"
    assert _osc_conflicts({"direction": "neutral", "macd_hist": -1.0}) == []
    assert _osc_conflicts({"direction": "bullish", "macd_hist": None,
                           "signals": {}}) == []


def test_brief_renders_warning_and_bar_stamp():
    out = format_brief({"ticker": "NOK", "price": PRICE,
                        "oscillator": [DAILY, WEEKLY]})
    assert "33/100" in out, "confluence must read as a score, not a count"
    assert "bar 2026-07-23" in out, "per-timeframe staleness must be visible"
    assert "internals disagree" in out
    assert "mf_curl down (vol-backed)" in out


def test_day_change_reconciles_with_printed_price():
    live = format_brief({"ticker": "NOK", "price": PRICE,
                         "intraday": {"current_price": 9.10,
                                      "change_pct": -7.6}})
    # -6.5% (9.10 vs 9.73), NOT the vendor's -7.6%, and the reference named
    assert "-6.5% vs 2026-07-23 close $9.73" in live, live.splitlines()[3]
    assert "-7.6%" not in live

    # No live quote: report that session's own move, never price vs itself
    closed = format_brief({"ticker": "NOK", "price": PRICE})
    assert "-5.3% on 2026-07-23" in closed
    assert "vs 2026-07-23 close $9.73" not in closed

    # No ret_1d and no live quote: omit rather than invent
    bare = format_brief({"ticker": "NOK", "price": dict(PRICE, ret_1d=None)})
    assert "Price $9.73 ·" in bare


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {name}: {e}")
    print(f"\n{'FAILED' if failed else 'All tests passed'}"
          f"{f' ({failed})' if failed else ''}")
    sys.exit(1 if failed else 0)
