"""
Regression tests for the brief's non-oscillator sections, from the Jul 25
lossy-summary audit (the sweep PR #147 asked for).

What these pin, per section:

- gamma: a missing level renders as "—", never "$0.00"; a null net GEX says
  "unavailable" instead of crashing; the wall-position doctrine renders on
  the same screen as the walls (stranded put wall above price; put+call on
  one strike = magnet).
- level ladder: decoration-magnitude gamma levels are NOT presented as
  levels (magnitude rule), and chart-level truncation is disclosed.
- patterns: detected date and distance-to-trigger render per row, and
  dropping lower-scored rows is disclosed, not silent.
- rotation: a missing RS percentile is omitted, not printed as "RS None/100".
- social: renders from social_buzz's REAL columns (sentiment / rank /
  mentions / grok_summary / snapshot_date) with the snapshot stamped; a
  missing Grok summary is labelled, not passed off as an AI read. The old
  code read keys that don't exist in the table, so the section was dead.
- alerts: truncation is disclosed.

Run: python3 tests/test_brief_sections.py   (or pytest)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.brief import format_brief  # noqa: E402

PRICE = {"as_of": "2026-07-23", "close": 30.09, "ret_1d": -0.2,
         "ret_1w": 1.6, "ret_1m": 12.5, "ret_3m": 6.0, "ret_6m": -8.0,
         "hi_52w": 47.08, "lo_52w": 24.55,
         "off_high_pct": -36.1, "off_low_pct": 22.6}

GAMMA_OK = {"as_of": "2026-07-24", "spot": 30.09, "call_wall": 32.0,
            "put_wall": 28.0, "gamma_flip": 30.5, "net_gex_bn": 1.42,
            "regime": "pinning", "magnitude": "load-bearing",
            "computed_at": "2026-07-24 08:15"}


def _brief(**kw):
    d = {"ticker": "JD", "price": PRICE}
    d.update(kw)
    return format_brief(d)


def test_gamma_missing_levels_render_as_dash_not_zero():
    gx = dict(GAMMA_OK, put_wall=None, gamma_flip=None, net_gex_bn=None,
              magnitude=None)
    out = _brief(gamma=gx)
    assert "flip —" in out, out
    assert "put wall — / call wall $32" in out
    assert "$0.00" not in out, "a missing level must not print as a price"
    assert "net GEX unavailable" in out


def test_gamma_stranded_put_wall_warns():
    # put wall ABOVE price = stranded pre-drop protection, never support
    out = _brief(gamma=dict(GAMMA_OK, put_wall=31.0))
    assert "stranded pre-drop" in out, out
    assert "never as support" in out
    # put wall below price: no stranded warning
    assert "stranded" not in _brief(gamma=GAMMA_OK)


def test_gamma_shared_strike_is_magnet_not_sr():
    out = _brief(gamma=dict(GAMMA_OK, put_wall=30.0, call_wall=30.0))
    assert "magnet/battleground" in out, out


def test_ladder_excludes_decoration_walls():
    lv = {"support": [{"price": 29.0, "stars": 4}],
          "resistance": [{"price": 31.0, "stars": 3}]}
    deco = dict(GAMMA_OK, net_gex_bn=0.012, magnitude="decoration")
    out = _brief(gamma=deco, levels=lv)
    assert "put wall (options)" not in out, \
        "decoration-magnitude walls must not be presented as levels"
    assert "options levels omitted" in out
    # load-bearing gamma: walls belong in the ladder
    assert "put wall (options)" in _brief(gamma=GAMMA_OK, levels=lv)


def test_ladder_disclosed_truncation():
    lv = {"support": [{"price": 29.0 - i, "stars": 3} for i in range(6)],
          "resistance": [{"price": 31.0 + i, "stars": 3} for i in range(5)]}
    out = _brief(levels=lv)
    assert "3 more mapped chart level(s)" in out, out


def test_patterns_show_detected_distance_and_disclose_truncation():
    row = {"timeframe": "daily", "pattern": "cup_handle", "direction": "long",
           "status": "forming", "trigger": 31.0, "target": 34.0,
           "invalid": 29.0, "last_close": 30.09, "dist_to_trigger_pct": 3.0,
           "score": 80.0, "detected": "2026-07-01"}
    rows = [dict(row, score=80.0 - i) for i in range(6)]
    out = _brief(patterns={"rows": rows, "stats": {
        "cup_handle": {"n": 1200, "hit_pct": 62.0, "win1r_pct": 71.0,
                       "avg_stop_r": -0.8}}})
    assert "detected 2026-07-01" in out, "detected date is a decision input"
    assert "+3.0% to trigger" in out
    assert "1 lower-scored pattern(s) not shown" in out
    assert "n=1,200" in out, "grades keep their sample size"


def test_rotation_missing_rs_is_omitted_not_none():
    rot = {"company_name": "JD.com", "sector": "Consumer Cyclical",
           "industry": "Internet Retail", "market_cap": 43.1e9,
           "rs_pct": None}
    out = _brief(rotation=rot)
    assert "RS None" not in out, out
    assert "RS 87/100" in _brief(rotation=dict(rot, rs_pct=87.0))


def test_social_renders_real_columns_with_snapshot_stamp():
    soc = {"sentiment": "bullish", "sentiment_score": 0.4, "rank": 59,
           "rank_24h_ago": 328, "mentions": 5, "grok_summary": None,
           "snapshot_date": "2026-07-21", "source": "wallstreetbets"}
    out = _brief(social=soc)
    assert "snapshot 2026-07-21" in out, "social must stamp its snapshot date"
    assert "sentiment bullish (+0.40)" in out
    assert "rank #59 (was #328 a day earlier)" in out
    assert "small-n" in out
    assert "not an AI read" in out, \
        "a missing Grok summary must be labelled, not implied"
    # with a summary present, the caveat goes away
    out2 = _brief(social=dict(soc, grok_summary="Traders debating the EU case."))
    assert "Traders debating the EU case." in out2
    assert "not an AI read" not in out2


def test_social_failed_grok_row_is_not_an_ai_read():
    # A row whose enrichment failed carries rank data but no summary —
    # it must never render as if Grok weighed in.
    soc = {"sentiment": None, "sentiment_score": None, "rank": 12,
           "rank_24h_ago": None, "mentions": 40, "grok_summary": None,
           "snapshot_date": "2026-07-24", "source": "all-stocks"}
    out = _brief(social=soc)
    assert "rank #12" in out
    assert "not an AI read" in out


def test_alerts_disclose_truncation():
    al = [{"date": f"2026-07-{10 + i:02d}", "type": "pattern",
           "signal": "breakout", "entry": 30.0 + i, "score": 80.0}
          for i in range(8)]
    out = _brief(alerts=al)
    assert "3 more in alert_log" in out, out


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
