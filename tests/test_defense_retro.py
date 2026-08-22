"""The retro defense read, pinned (2026-08-22).

Research over the desk's own past touch fills — same detector as the
live shadow, separate table, and structurally unable to touch either
the paper tables or the live shadow record.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.defense_retro import _shadow_r  # noqa: E402


def test_shadow_r_repriced_from_defense_entry():
    # Live: in 100.00, out 103.00. Shadow defended at 100.40, stop 97.
    # Shadow risk 3.40, shadow gain 2.60 -> 0.7647R (vs live +1.0R):
    # the premium is the cost of confirmation, visible in the number.
    r = _shadow_r("defended", 100.40, 97.0, 103.0)
    assert abs(r - (103.0 - 100.40) / (100.40 - 97.0)) < 1e-9


def test_skips_are_zero_and_holes_are_none():
    assert _shadow_r("knife_skipped", None, 97.0, 92.0) == 0.0
    assert _shadow_r("missed", None, 97.0, 105.0) == 0.0
    assert _shadow_r("no_defense", None, 97.0, 99.0) == 0.0
    assert _shadow_r("unavailable", None, 97.0, 99.0) is None
    assert _shadow_r("defended", 100.4, 97.0, None) is None   # still open
    assert _shadow_r("defended", 96.0, 97.0, 99.0) is None    # bad geometry


def test_research_only_by_signature():
    from analysis import defense_retro
    src = inspect.getsource(defense_retro)
    for forbidden in ("UPDATE paper_trades", "INSERT INTO paper_trades",
                      "UPDATE paper_specs", "INSERT INTO paper_specs",
                      "INSERT INTO paper_defense_shadow",
                      "UPDATE paper_defense_shadow"):
        assert forbidden not in src, forbidden
    # Same detector as the live shadow — one definition, three reads
    # (live shadow, historical study, retro).
    assert "find_defense" in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
