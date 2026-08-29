"""The AI-capex basket, pinned (2026-08-29).

  1. Membership is Eric's taxonomy verbatim: NVDA is the bellwether,
     the capex layer rides with it, and the software platforms
     (MSFT, PLTR) are deliberately OUT.
  2. Read-only by signature — the basket renders state, it never
     writes anything anywhere.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.ai_capex import AI_CAPEX, BELLWETHER  # noqa: E402


def test_membership_is_erics_taxonomy():
    assert BELLWETHER == "NVDA" and "NVDA" in AI_CAPEX
    for capex in ("AVGO", "AMD", "MU", "TSM", "ARM", "VRT", "SMCI",
                  "ANET", "CRWV"):
        assert capex in AI_CAPEX
    for software in ("MSFT", "PLTR"):
        assert software not in AI_CAPEX
    for separate_question in ("META", "GOOGL", "AMZN"):
        assert separate_question not in AI_CAPEX


def test_read_only_by_signature():
    from analysis import ai_capex
    src = inspect.getsource(ai_capex)
    assert "INSERT INTO" not in src
    assert "UPDATE " not in src and "DELETE FROM" not in src


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
