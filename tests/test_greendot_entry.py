"""The green-dot entry-schedule study's pure core, pinned (2026-08-29).

  1. Heikin Ashi is the classic recursion — and HA values are DISPLAYS:
     the study enters at REAL closes, never at an HA price (by
     signature check on the module source).
  2. Eric's doji spec verbatim: body <= 15% of the HA bar's range.
  3. The two HA sub-variants differ exactly on the break requirement:
     any green after a doji vs green CLOSING above the doji's HA high.
  4. A window with no trigger is a named miss, not a silent skip —
     missed_runner if price ended above the dot, still_falling below.
  5. The study writes only its own table — by signature.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.greendot_entry_study import (DOJI_BODY_MAX,  # noqa: E402
                                           find_ha_entry, find_raw_green,
                                           heikin_ashi, is_doji, _miss)


def _bar(o, h, l, c):
    return dict(o=o, h=h, l=l, c=c)


def test_heikin_ashi_recursion():
    bars = [_bar(10, 12, 9, 11), _bar(11, 13, 10, 12)]
    ha = heikin_ashi(bars)
    # Bar 0: hc = (10+12+9+11)/4 = 10.5, ho = (10+11)/2 = 10.5
    assert ha[0]["hc"] == 10.5 and ha[0]["ho"] == 10.5
    # Bar 1: hc = (11+13+10+12)/4 = 11.5, ho = avg(prior ho, prior hc)
    assert ha[1]["hc"] == 11.5 and ha[1]["ho"] == 10.5
    # Highs/lows include the synthetic open/close.
    assert ha[1]["hh"] == 13 and ha[1]["hl"] == 10


def test_ha_high_low_include_synthetic_values():
    # A gap-down real bar whose HA open sits above the real high.
    bars = [_bar(100, 101, 99, 100), _bar(90, 91, 89, 90)]
    ha = heikin_ashi(bars)
    assert ha[1]["ho"] == 100.0          # avg(100, 100)
    assert ha[1]["hh"] == 100.0          # ho above the real high 91
    assert ha[1]["hl"] == 89


def test_doji_is_small_body_relative_to_range():
    assert DOJI_BODY_MAX == 0.15         # Eric's calibration, frozen
    # Range 10, body 1.4 → 14% → doji.
    assert is_doji(dict(ho=100.0, hc=101.4, hh=106, hl=96))
    # Range 10, body 1.6 → 16% → not a doji.
    assert not is_doji(dict(ho=100.0, hc=101.6, hh=106, hl=96))
    # Zero-range bar can never be a doji (guard, not a signal).
    assert not is_doji(dict(ho=100.0, hc=100.0, hh=100, hl=100))


def _ha_seq():
    # idx 0: dot bar (red). idx 1: doji (body 0.5 of range 10, high 105).
    # idx 2: green but closes BELOW the doji high. idx 3: another doji.
    # idx 4: green closing ABOVE doji 3's high.
    return [
        dict(ho=110.0, hc=100.0, hh=111, hl=99),
        dict(ho=100.0, hc=100.5, hh=105, hl=95),
        dict(ho=100.5, hc=103.0, hh=104, hl=100),
        dict(ho=103.0, hc=103.4, hh=106, hl=101),
        dict(ho=103.4, hc=108.0, hh=109, hl=103),
    ]


def test_ha_any_takes_first_green_after_doji():
    assert find_ha_entry(_ha_seq(), 0, 10, require_break=False) == 2


def test_ha_brk_demands_close_above_doji_high():
    # Bar 2's close 103.0 < doji high 105 → refused; bar 4 closes 108 >
    # doji-3's high 106 → the break variant enters two bars later.
    assert find_ha_entry(_ha_seq(), 0, 10, require_break=True) == 4


def test_ha_entry_respects_window():
    # Window ending at 2 means bar 2 is the last GREEN candidate the
    # any-variant can take; the break variant finds nothing in window.
    assert find_ha_entry(_ha_seq(), 0, 2, require_break=False) == 2
    assert find_ha_entry(_ha_seq(), 0, 2, require_break=True) is None


def test_raw_green_finds_first_green_close():
    bars = [_bar(10, 11, 9, 9.5), _bar(9.5, 10, 9, 9.2),
            _bar(9.2, 10, 9, 9.8), _bar(9.8, 11, 9.5, 10.5)]
    assert find_raw_green(bars, 0, 10) == 2
    assert find_raw_green(bars[:2], 0, 10) is None


def test_miss_reasons_are_named():
    daily = [_bar(10, 10, 10, 10), _bar(10, 10, 10, 12),
             _bar(12, 12, 12, 8)]
    assert "missed_runner" in _miss(daily, 0, 1, 10.0)
    assert "still_falling" in _miss(daily, 0, 2, 10.0)
    assert "beyond recorded history" in _miss(daily, 2, 2, 10.0)


def test_entries_are_real_closes_never_ha_prices():
    from analysis.greendot_entry_study import _one_ticker
    src = inspect.getsource(_one_ticker)
    # Every entry price in the write path is a real daily close (or the
    # ladder's limit levels); HA values never appear in the writer —
    # they are consumed inside find_ha_entry as SIGNALS only.
    assert 'daily[edi]["c"]' in src
    assert '"hc"' not in src and '"ho"' not in src


def test_writes_only_its_own_table():
    from analysis import greendot_entry_study
    src = inspect.getsource(greendot_entry_study)
    assert "INSERT INTO greendot_entry" in src
    assert "UPDATE " not in src and "DELETE FROM" not in src
    assert "INSERT INTO paper_" not in src
    assert "INSERT INTO greendot_dots" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
