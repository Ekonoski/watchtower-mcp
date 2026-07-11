"""
Chart-pattern backtest: how long does each pattern take to play out?

Replays the SAME detectors the live scanner uses (analysis.pattern_scan.
detect_patterns) as-of every Nth historical session, so each detection is
exactly what the scanner would have shown that evening — same tolerances,
same spent-move rule, same freshness windows, no lookahead. Every fresh
BREAKOUT found in the replay is tracked forward to its resolution:

  outcome 'target'  — measured-move objective touched (bars_to_outcome)
  outcome 'invalid' — closed through the invalidation level first
  outcome 'open'    — neither within the horizon (130 bars) / end of data

The point is Eric's options question: median bars-from-breakout-to-target
per pattern is the empirical answer to "what expiry do I buy?" — the
Patterns tab converts these bar counts into an estimated-resolution window
and a suggested DTE. Daily bars only in v1 (weekly history is too short
for a sample; the bar-count stats transfer across timeframes on the
fractal assumption — a pattern takes similar BAR counts at any scale).

Results land in pattern_backtest (migration 0068), UNIQUE per
(ticker, pattern, breakout_date) — idempotent to re-run. Seeds once at
deploy while the table is empty; ~1,500-name deterministic sample of the
universe (PATTERN_BT_SAMPLE env overrides), roughly 10 minutes.
"""
import logging
import os
import time
from datetime import date

log = logging.getLogger(__name__)

STEP = int(os.environ.get("PATTERN_BT_STEP", "5"))        # as-of stride, bars
SAMPLE = int(os.environ.get("PATTERN_BT_SAMPLE", "1500"))  # universe sample
HORIZON = 130          # bars to wait for resolution before calling it open
WINDOW = 420           # bars of history per as-of detection (covers max lookback)
MIN_BARS = 90


def _bars_for(conn, tickers: list) -> dict:
    """Daily bar dicts per ticker (wick-aware, oldest → newest)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, trade_date, COALESCE(open, close),
                   COALESCE(high, close), COALESCE(low, close), close,
                   COALESCE(volume, 0)
            FROM daily_prices
            WHERE ticker = ANY(%s) AND trade_date >= CURRENT_DATE - 1200
            ORDER BY ticker, trade_date
        """, (tickers,))
        rows = cur.fetchall()
    out: dict = {}
    for t, d, o, h, lo, c, v in rows:
        if c is None:
            continue
        out.setdefault(t, []).append(
            {"date": d, "open": float(o), "high": float(h),
             "low": float(lo), "close": float(c), "volume": float(v)})
    return out


def _breakout_idx(bars: list, as_of: int, trigger: float, direction: str,
                  break_recent: int):
    """Full-series index of the close that crossed the trigger — the actual
    breakout bar. _status only reports 'breakout' when this cross happened
    within break_recent bars of as_of, so scan just that tail."""
    lo = max(1, as_of - break_recent)
    for j in range(lo, as_of + 1):
        c, p = bars[j]["close"], bars[j - 1]["close"]
        if direction == "bullish" and c > trigger >= p:
            return j
        if direction == "bearish" and c < trigger <= p:
            return j
    return as_of


def _resolve(bars: list, j: int, target: float, invalid, direction: str):
    """Walk forward from the breakout bar: measured-move touch vs a CLOSE
    through the invalidation level (closes define structure; wicks don't).
    Same-bar tie goes to 'invalid' — the conservative read."""
    end = min(len(bars), j + 1 + HORIZON)
    for i in range(j + 1, end):
        b = bars[i]
        stopped = (invalid is not None and
                   (b["close"] < invalid if direction == "bullish"
                    else b["close"] > invalid))
        hit = (b["high"] >= target if direction == "bullish"
               else b["low"] <= target)
        if stopped:
            return "invalid", i - j
        if hit:
            return "target", i - j
    return "open", None


def _replay_ticker(bars: list) -> list:
    """All fresh breakouts the live scanner would have flagged for one
    ticker, each tracked to resolution. Dedupe on the breakout bar."""
    from analysis.pattern_scan import detect_patterns, TF
    n = len(bars)
    if n < MIN_BARS + 10:
        return []
    break_recent = TF["daily"]["break_recent"]
    dates = [b["date"] for b in bars]
    seen = set()
    out = []
    for as_of in range(MIN_BARS, n, STEP):
        window = bars[max(0, as_of + 1 - WINDOW):as_of + 1]
        for det in detect_patterns(window, "daily"):
            if det["status"] != "breakout":
                continue
            j = _breakout_idx(bars, as_of, det["trigger_price"],
                              det["direction"], break_recent)
            key = (det["pattern"], j)
            if key in seen:
                continue
            seen.add(key)
            outcome, bto = _resolve(bars, j, det["target"],
                                    det["invalid_level"], det["direction"])
            anchor = det.get("anchor_date")
            width = None
            if anchor is not None:
                try:
                    width = j - dates.index(anchor)
                except ValueError:
                    pass
            out.append((det["pattern"], det["direction"], anchor, dates[j],
                        width, det["trigger_price"], det["target"],
                        det["invalid_level"], outcome, bto))
    return out


def run_pattern_backtest() -> dict:
    """Replay a deterministic sample of the universe and store results."""
    from screen.reversal_screen import _conn
    from psycopg2.extras import execute_values
    conn = _conn()
    t0 = time.time()
    try:
        try:
            with conn.cursor() as _c:
                _c.execute("SET statement_timeout = '600s'")
            conn.commit()
        except Exception:
            pass
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker FROM screener_snapshot
                UNION SELECT ticker FROM watchlist WHERE active = true
            """)
            universe = sorted({r[0] for r in cur.fetchall() if r[0]})
        if len(universe) > SAMPLE:
            stride = len(universe) / SAMPLE
            universe = [universe[int(i * stride)] for i in range(SAMPLE)]
        log.info(f"[patterns] backtest replay over {len(universe)} names "
                 f"(step {STEP} bars)")
        rows = []
        for i in range(0, len(universe), 120):
            frames = _bars_for(conn, universe[i:i + 120])
            for t, bars in frames.items():
                try:
                    for ev in _replay_ticker(bars):
                        rows.append((t,) + ev)
                except Exception as e:
                    log.debug(f"[patterns] backtest {t} failed: {e}")
            if i % 600 == 0 and i:
                log.info(f"[patterns] backtest {i}/{len(universe)} names, "
                         f"{len(rows)} breakouts so far")
        if rows:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO pattern_backtest
                        (ticker, pattern, direction, anchor_date,
                         breakout_date, base_width_bars, trigger_price,
                         target, invalid_level, outcome, bars_to_outcome)
                    VALUES %s
                    ON CONFLICT (ticker, pattern, breakout_date) DO UPDATE SET
                        outcome = EXCLUDED.outcome,
                        bars_to_outcome = EXCLUDED.bars_to_outcome
                """, rows, page_size=2000)
            conn.commit()
        log.info(f"[patterns] backtest stored {len(rows)} breakouts "
                 f"in {time.time() - t0:.0f}s")
        return {"breakouts": len(rows), "tickers": len(universe),
                "seconds": round(time.time() - t0)}
    finally:
        conn.close()


def timing_stats(conn=None) -> dict:
    """Per-pattern timing: {pattern: {n, hit_rate, p25, med, p75}} in BARS
    from breakout to target-touch (winners only for the time stats)."""
    from screen.reversal_screen import _conn
    own = conn is None
    if own:
        conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pattern, count(*) AS n,
                       round(avg(CASE WHEN outcome='target' THEN 1.0
                                      WHEN outcome='invalid' THEN 0.0 END) * 100, 1),
                       percentile_cont(0.25) WITHIN GROUP (ORDER BY bars_to_outcome)
                           FILTER (WHERE outcome='target'),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY bars_to_outcome)
                           FILTER (WHERE outcome='target'),
                       percentile_cont(0.75) WITHIN GROUP (ORDER BY bars_to_outcome)
                           FILTER (WHERE outcome='target')
                FROM pattern_backtest
                GROUP BY pattern
            """)
            out = {}
            for p, n, hr, p25, med, p75 in cur.fetchall():
                out[p] = {"n": int(n),
                          "hit_rate": float(hr) if hr is not None else None,
                          "p25": float(p25) if p25 is not None else None,
                          "med": float(med) if med is not None else None,
                          "p75": float(p75) if p75 is not None else None}
            return out
    finally:
        if own:
            conn.close()


# ── Estimated resolution for live rows ───────────────────────────────────────

# A pattern takes a similar number of BARS to resolve at any scale — convert
# bar counts to calendar time per timeframe (4h ≈ 12 bars/trading week).
BARS_PER_WEEK = {"daily": 5.0, "weekly": 1.0, "4h": 12.0}
DTE_TENORS = (21, 30, 45, 60, 90, 120, 180, 270, 365)


def estimate_resolution(pattern: str, timeframe: str, anchor_date,
                        stats: dict) -> dict:
    """{'weeks_lo', 'weeks_hi', 'dte', 'source'} — measured when the
    backtest has a sample for this pattern (p25–p75 bars from breakout to
    target), else the width heuristic (a base resolves in ⅓–1× its build
    time; flags in ½–1× their pole)."""
    bpw = BARS_PER_WEEK.get(timeframe, 5.0)
    st = (stats or {}).get(pattern) or {}
    if st.get("n", 0) >= 30 and st.get("p25") is not None:
        lo, hi = st["p25"] / bpw, st["p75"] / bpw
        source = "measured"
    else:
        if anchor_date is None:
            return {}
        try:
            span_days = (date.today() - anchor_date).days
        except Exception:
            return {}
        weeks = max(span_days / 7.0, 1.0)
        frac = (0.5, 1.0) if "flag" in pattern else (0.33, 1.0)
        lo, hi = weeks * frac[0], weeks * frac[1]
        source = "width"
    lo, hi = max(round(lo, 1), 0.5), max(round(hi, 1), 1.0)
    want = hi * 7 * 2                       # 2x the upper estimate, in days
    dte = next((t for t in DTE_TENORS if t >= want), DTE_TENORS[-1])
    return {"weeks_lo": lo, "weeks_hi": hi, "dte": dte, "source": source}
