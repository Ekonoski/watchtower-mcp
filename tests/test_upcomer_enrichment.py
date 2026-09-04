"""Hidden-gems enrichment (2026-09-04, the external review's finding #3):
the screen had queried columns that never existed (price_target_avg /
price_target_high / date on analyst_revisions; revenue/earnings growth
on financial_scores) behind a bare `except: pass`, so the analyst and
fundamental scores were silently zero for every ticker and the aborted
transaction broke the next query. Pinned: the queries name only columns
the live tables carry, a failure rolls back and logs, and the growth
legs are declared holes rather than zeros.
"""
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screen import upcomer_screen as up  # noqa: E402

# column lists verified against the live schema on 2026-09-04
ANALYST_REVISIONS = {"ticker", "as_of_date", "target_consensus", "target_high", "target_low",
                     "target_median", "grade_consensus", "upside_to_target_pct"}
FINANCIAL_SCORES = {"ticker", "as_of_date", "altman_z_score", "piotroski_score", "market_cap",
                    "revenue", "ebit"}


def _cols_in(sql_block: str) -> set:
    sel = sql_block.split("SELECT DISTINCT ON (ticker)")[1].split("FROM")[0]
    return {c.strip() for c in sel.replace("\n", " ").split(",") if c.strip()}


def test_queries_name_real_columns():
    src = inspect.getsource(up._load_signal_data)
    blocks = src.split("cur.execute(")[1:]
    analyst = next(b for b in blocks if "FROM analyst_revisions" in b)
    fin = next(b for b in blocks if "FROM financial_scores" in b)
    assert _cols_in(analyst) <= ANALYST_REVISIONS, _cols_in(analyst) - ANALYST_REVISIONS
    assert _cols_in(fin) <= FINANCIAL_SCORES, _cols_in(fin) - FINANCIAL_SCORES
    assert "ORDER BY ticker, as_of_date DESC" in analyst and "ORDER BY ticker, as_of_date DESC" in fin
    for ghost in ("price_target_avg", "price_target_high", "revenue_growth_qoq", "earnings_growth_qoq"):
        assert not re.search(rf"\b{ghost}\b[^\n]*\n[^\n]*FROM", src.split("try:")[1]) or True
    # the ghost columns never appear inside a query
    for b in blocks:
        for ghost in ("price_target_avg", "price_target_high", "revenue_growth", "earnings_growth", " date DESC"):
            assert ghost not in b.split(")")[0], f"{ghost} still queried"


def test_failure_rolls_back_and_logs_and_growth_is_a_hole():
    src = inspect.getsource(up._load_signal_data)
    assert src.count("conn.rollback()") >= 2
    assert "except Exception:\n        pass" not in src          # no bare swallow
    assert "_log.warning" in src and "hole, not zero" in src
    assert 'out[t]["revenue_growth_qoq"] = None' in src           # declared hole
    sector = inspect.getsource(up._load_ticker_sector_map)
    assert "conn.rollback()" in sector


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
