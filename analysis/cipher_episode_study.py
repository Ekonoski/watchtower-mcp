"""
Cipher-at-episodes study — the FULL market-cipher recreation graded against
the v6 replay record (Eric, 2026-08-11: "test the cipher recreation and
money flow like we did MACD and RSI").

The oscillator-lite study (issue #177, 2026-08-11) reconstructed RSI+MACD
in SQL and found weekly mid-band breakouts pay ~4x overbought ones. This
module runs the REAL engine — analysis.oscillator's compute_oscillator /
evaluate_signals, byte-for-byte the code that fires live alerts — across
every v6 episode's breakout bar, so wavetrend position, money-flow state,
curls, springs, and the confluence composite get graded on the same 358k
episodes. A SQL re-implementation of wavetrend was rejected on the
render-the-columns-the-table-has principle: test the indicator we trade,
not a lookalike.

No lookahead by construction: every indicator in compute_oscillator is
EMA/SMA/rolling — strictly backward-looking — so computing once over the
full series and evaluating at bar i equals recomputing on bars [0..i].
tests/test_cipher_study.py pins that equivalence on a synthetic series.

pattern_ctx is deliberately None for every evaluation: the structure
bucket (20 pts) would be a constant echo of "there was a pattern here"
(every episode IS a pattern), so confluence is graded on its 0-80
oscillator core, stated wherever the numbers surface.

Ops: resumable per ticker (cipher_study_done), budget-capped per run,
completion marker 'cipher_episode_study_complete' in scheduler_job_claims.
The scheduler's boot seeder holds until after the close on trading days —
the live desk owns daytime database I/O.
"""
import datetime as dt
import json
import logging
import os
import sys
import time

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.oscillator import compute_oscillator, evaluate_signals, resample_weekly  # noqa: E402

log = logging.getLogger("watchtower.cipher_study")

BUDGET_SECS = int(os.environ.get("CIPHER_STUDY_BUDGET_SECS", "3300"))
COMPLETE_MARKER = "cipher_episode_study_complete"

DDL = """
CREATE TABLE IF NOT EXISTS cipher_episode_state (
  ticker text NOT NULL,
  timeframe text NOT NULL,
  breakout_date date NOT NULL,
  confluence int,
  direction text,
  rsi numeric,
  macd_hist_pos boolean,
  wt2 numeric,
  mf numeric,
  mf_slope_pos boolean,
  signals jsonb,
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (ticker, timeframe, breakout_date));
CREATE TABLE IF NOT EXISTS cipher_study_done (ticker text PRIMARY KEY);
"""


def state_at(ind: pd.DataFrame, i: int) -> dict:
    """Cipher state AT bar i (0-based) of a precomputed indicator frame.
    Slices the frame through bar i and runs the live evaluate_signals on
    it — identical to having computed everything as-of that bar."""
    view = ind.iloc[:i + 1]
    ev = evaluate_signals(view, pattern_ctx=None)
    c = view.iloc[-1]
    f = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
    mf_now, mf_prev = f(c.get("mf_candle")), None
    if len(view) >= 2:
        mf_prev = f(view.iloc[-2].get("mf_candle"))
    return {
        "confluence": int(ev.get("confluence_score") or 0),
        "direction": ev.get("direction"),
        "rsi": f(c.get("rsi")),
        "macd_hist_pos": (None if f(c.get("macd_hist")) is None
                          else bool(c["macd_hist"] > 0)),
        "wt2": f(c.get("wt2")),
        "mf": mf_now,
        "mf_slope_pos": (None if mf_now is None or mf_prev is None
                         else bool(mf_now > mf_prev)),
        "signals": ev.get("signals") or {},
    }


def _frame(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").astype(float)
    return df


def _locate(index: pd.DatetimeIndex, bd) -> int:
    """Bar position for a breakout date: exact match, else the last bar at
    or before it — but only within 6 calendar days (weekly frames stamp
    the week's final session; the replay's frames may stamp mid-week when
    history ends there). Returns -1 when nothing sane matches."""
    ts = pd.Timestamp(bd)
    i = int(index.searchsorted(ts, side="right")) - 1
    if i < 0:
        return -1
    if (ts - index[i]).days > 6:
        return -1
    return i


def run(budget_secs: int = BUDGET_SECS) -> bool:
    """Process tickers until done or out of budget. Returns True when the
    whole study is complete (marker written)."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute(DDL)
            c.execute("SET statement_timeout = '600s'")
            c.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s LIMIT 1",
                      (COMPLETE_MARKER,))
            if c.fetchone():
                log.info("[cipher-study] already complete — no-op")
                return True
            c.execute("""SELECT DISTINCT ticker FROM pattern_backtest WHERE bt_version=6
                         AND ticker NOT IN (SELECT ticker FROM cipher_study_done)
                         ORDER BY ticker""")
            todo = [r[0] for r in c.fetchall()]
        conn.commit()
        log.info(f"[cipher-study] {len(todo)} tickers to go")
        started = time.monotonic()
        done_now = 0
        for tk in todo:
            if time.monotonic() - started > budget_secs:
                log.info(f"[cipher-study] budget reached after {done_now} tickers — "
                         f"{len(todo) - done_now} resume next run")
                return False
            try:
                with conn.cursor() as c:
                    c.execute("""SELECT trade_date, COALESCE(open, close), COALESCE(high, close),
                                        COALESCE(low, close), close, COALESCE(volume, 0)
                                 FROM daily_prices WHERE ticker=%s AND close IS NOT NULL
                                 ORDER BY trade_date""", (tk,))
                    bars = c.fetchall()
                    c.execute("""SELECT timeframe, breakout_date FROM pattern_backtest
                                 WHERE bt_version=6 AND ticker=%s
                                 GROUP BY timeframe, breakout_date""", (tk,))
                    eps = c.fetchall()
                out_rows = []
                if len(bars) >= 80 and eps:
                    daily = _frame(bars)
                    ind = {"daily": compute_oscillator(daily)}
                    if any(tf == "weekly" for tf, _ in eps):
                        wk = resample_weekly(daily, drop_partial=True)
                        if len(wk) >= 80:
                            ind["weekly"] = compute_oscillator(wk)
                    for tf, bd in eps:
                        f = ind.get(tf)
                        if f is None:
                            continue
                        i = _locate(f.index, bd)
                        if i < 70:          # evaluate_signals' own floor
                            continue
                        st = state_at(f, i)
                        out_rows.append((tk, tf, bd, st["confluence"], st["direction"],
                                         st["rsi"], st["macd_hist_pos"], st["wt2"],
                                         st["mf"], st["mf_slope_pos"],
                                         json.dumps(st["signals"], default=str)))
                with conn.cursor() as c:
                    if out_rows:
                        execute_values(c,
                            """INSERT INTO cipher_episode_state
                               (ticker, timeframe, breakout_date, confluence, direction,
                                rsi, macd_hist_pos, wt2, mf, mf_slope_pos, signals)
                               VALUES %s ON CONFLICT DO NOTHING""",
                            out_rows, page_size=500)
                    c.execute("INSERT INTO cipher_study_done VALUES (%s) ON CONFLICT DO NOTHING",
                              (tk,))
                conn.commit()
                done_now += 1
            except Exception as e:
                conn.rollback()
                # Loud per-ticker failure, ticker NOT marked done — it retries
                # next run. The v5 lesson: a quiet per-ticker except is how a
                # study silently censors itself.
                log.warning(f"[cipher-study] {tk} FAILED (will retry): {e}")
        with conn.cursor() as c:
            c.execute("""SELECT count(*) FROM (
                           SELECT DISTINCT ticker FROM pattern_backtest WHERE bt_version=6
                           EXCEPT SELECT ticker FROM cipher_study_done) x""")
            remaining = c.fetchone()[0]
            if remaining == 0:
                c.execute("""INSERT INTO scheduler_job_claims (job_name, run_date, claimed_at)
                             VALUES (%s, CURRENT_DATE, now())
                             ON CONFLICT (job_name, run_date) DO NOTHING""",
                          (COMPLETE_MARKER,))
                conn.commit()
                log.info("[cipher-study] COMPLETE — marker written")
                return True
            log.info(f"[cipher-study] {remaining} tickers still failing/pending — "
                     "no marker; next run retries")
            conn.commit()
            return False
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
