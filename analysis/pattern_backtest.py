"""
Chart-pattern backtest: how long does each pattern take to play out?

Replays the SAME detectors the live scanner uses (analysis.pattern_scan.
detect_patterns) as-of historical sessions, so each detection is exactly
what the scanner would have shown that evening. Every fresh BREAKOUT is
tracked forward to its resolution:

  outcome 'target'  — measured-move objective touched (bars_to_outcome)
  outcome 'invalid' — closed through the invalidation level first
  outcome 'open'    — neither within the horizon (130 bars) / end of data

BT_VERSION 2 — the measurement rules, hardened after a five-way
adversarial audit of v1 found its stats inflated:
  * ENTRY IS THE NEXT BAR'S OPEN after the evening the cross printed —
    never the trigger price. v1 booked fills at the trigger even though
    the signal is only knowable at that bar's close; on tight-stop EMA
    patterns that gifted ~0.5R per trade and ~20% of "entries" were
    already past +1R. All R math (stop distance, +1R trim, realized_r)
    anchors at entry_price now.
  * NO BACKDATING: a breakout is entered when the replay grid first sees
    it (cross within the last STEP bars), not excavated up to 7 bars back
    — v1's backdating let the status filter silently drop breakouts that
    had already thrown back, censoring post-entry losers.
  * POINT-IN-TIME UNIVERSE: every ticker with enough history in
    daily_prices — including delisted names — not today's survivor
    screen. (Residual bias remains where delisted histories are missing
    from daily_prices; backfill narrows it over time.)
  * FULL WINDOW: history from 2021-06 (all we store), so the 2022 bear
    is in-sample instead of structurally excluded.
  * EPISODE DEDUP: one event per (ticker, pattern) until the prior event
    resolves — evolving formations no longer emit correlated duplicates.
  * REGIME TAG: each event stores spy_above (SPY vs its 200-day SMA on
    entry day) so every stat can be split by tape.
  * realized_r records the true loss size on stops (close through the
    level, not -1R), and engine_version/bt_version stamp provenance;
    the table is truncated when BT_VERSION advances so retired-engine
    rows can never blend in again.

Results land in pattern_backtest, UNIQUE per (ticker, pattern,
breakout_date) — idempotent to re-run, inserted incrementally per batch
so an interrupted run keeps its progress.
"""
import logging
import os
import time
from datetime import date

log = logging.getLogger(__name__)

BT_VERSION = 6   # v6: v5's deep window + the path-1 resolve fix. Since v4,
# the fresh-breakout path unpacked 6 values from a 7-value _resolve — the
# first such episode on any ticker raised ValueError and the per-ticker
# guard silently discarded the ENTIRE ticker at DEBUG level. v4/v5 priors
# were therefore graded on the SURVIVORS (tickers whose episodes all came
# through the armed-trigger path) — a censored sample wearing a full-
# universe label. Every class prior must be re-read from v6.
# v5: deep regime window (2005+) — graded through 2008, 2011,
# 2015, 2018, COVID, and 2022, not just the 2021+ tape (decided 2026-08-08:
# regime risk is the desk's #1 holdup, and the answer is data). The window is
# part of the measurement, so the bump truncates v4 — priors from a one-bear
# sample must never blend with priors from a five-bear one.
#
# SURVIVORSHIP, stated where the numbers are made: deep history exists only
# for names alive in daily_prices since 2021 — names that died before then
# never entered the table, so pre-2021 grades come from survivors and every
# bear-market stat reads OPTIMISTIC. Cite it beside any v5 prior the way n
# is cited. (Delisted-universe onboarding narrows this; its own project.)
# Timeframes the seeder replays. Weekly bars are aggregated from daily_prices;
# 4h needs intraday history and its own bounded universe — deliberately absent
# until built as its own job.
BACKTEST_TIMEFRAMES = [t.strip() for t in
                       os.environ.get("PATTERN_BT_TIMEFRAMES", "daily,weekly").split(",") if t.strip()]
STEP = int(os.environ.get("PATTERN_BT_STEP", "2"))        # as-of stride, bars
SAMPLE = int(os.environ.get("PATTERN_BT_SAMPLE", "2500"))  # universe sample
HISTORY_START = os.environ.get("PATTERN_BT_HISTORY_START", "2005-01-01")
# ^ served by ingestion/backfill_daily_history.py (watchtower repo), which
# backfills THIS engine's deterministic sample. A sampled ticker whose deep
# history hasn't landed (or never existed) replays its shallow window —
# graceful degradation, identical to v4 behavior for that name.
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
            WHERE ticker = ANY(%s) AND trade_date >= %s
            ORDER BY ticker, trade_date
        """, (tickers, HISTORY_START))
        rows = cur.fetchall()
    out: dict = {}
    for t, d, o, h, lo, c, v in rows:
        if c is None:
            continue
        out.setdefault(t, []).append(
            {"date": d, "open": float(o), "high": float(h),
             "low": float(lo), "close": float(c), "volume": float(v)})
    return out


def _weekly_bars(daily: list) -> list:
    """ISO-week bars from daily bars; each bar dated by its last session so
    spy-regime and calendar lookups land on real trading dates."""
    out, cur_key = [], None
    for b in daily:
        k = b["date"].isocalendar()[:2]
        if k != cur_key:
            out.append(dict(b))
            cur_key = k
        else:
            w = out[-1]
            w["date"] = b["date"]
            w["high"] = max(w["high"], b["high"])
            w["low"] = min(w["low"], b["low"])
            w["close"] = b["close"]
            w["volume"] += b["volume"]
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


def _resolve(bars: list, j: int, entry: float, target: float, invalid,
             direction: str, trigger: float = None):
    """Walk forward from the ENTRY bar (j+1, filled at its open): measured-
    move touch vs a CLOSE through the invalidation level (closes define
    structure; wicks don't). Same-bar ties go to 'invalid' — the
    conservative read. +1R is one stop-distance beyond the ENTRY price —
    the first executable fill — not the trigger; that half-R of "free"
    fill was v1's largest inflation. realized_r on stops records the true
    loss (stop-close vs entry, in R), which routinely exceeds -1R.
    retest_bar = bars after the breakout until price first re-touches the
    TRIGGER (wick counts) — the second-chance entry the retest study measures;
    None when it never comes back inside the horizon.
    Returns (outcome, bars_to_outcome, win_1r, bars_to_1r, realized_r,
    retest_bar, end_index)."""
    r1 = risk = None
    if invalid is not None and abs(entry - invalid) > 1e-9:
        risk = abs(entry - invalid)
        r1 = entry + risk if direction == "bullish" else entry - risk
    win_1r = False if r1 is not None else None
    bars_1r = None
    retest = None
    end = min(len(bars), j + 1 + HORIZON)
    for i in range(j + 1, end):
        b = bars[i]
        if retest is None and trigger is not None:
            if (b["low"] <= trigger if direction == "bullish"
                    else b["high"] >= trigger):
                retest = i - j
        stopped = (invalid is not None and
                   (b["close"] < invalid if direction == "bullish"
                    else b["close"] > invalid))
        hit = (b["high"] >= target if direction == "bullish"
               else b["low"] <= target)
        if r1 is not None and win_1r is False and not stopped:
            if (b["high"] >= r1 if direction == "bullish" else b["low"] <= r1):
                win_1r, bars_1r = True, i - j
        if stopped:
            rr = None
            if risk:
                rr = round((b["close"] - entry) / risk
                           if direction == "bullish"
                           else (entry - b["close"]) / risk, 3)
            return "invalid", i - j, win_1r, bars_1r, rr, retest, i
        if hit:
            if win_1r is False:
                win_1r, bars_1r = True, i - j
            rr = None
            if risk:
                rr = round(abs(target - entry) / risk, 3)
            return "target", i - j, win_1r, bars_1r, rr, retest, i
    return "open", None, win_1r, bars_1r, None, retest, end - 1


def _replay_ticker(bars: list, spy_above: dict, timeframe: str = "daily") -> list:
    """All fresh breakouts the live scanner would have flagged for one
    ticker, entered at the NEXT bar's open, each tracked to resolution.
    One live episode per pattern at a time (no correlated duplicates).

    Two trigger paths, mirroring production:
      1. status == 'breakout' detections — patterns whose detector still
         reports them after the cross (bases, H&S, wedges, ...).
      2. armed forming triggers (BT v3) — flags/triangles DISSOLVE the
         bar they break out (a flag with a new high is no longer a
         flag), so their detectors can never emit 'breakout' and v2
         silently never graded them. Live, the trigger-alert layer
         watches the stored trigger price directly; the replay now does
         the same: a forming pattern arms its trigger, each grid step
         scans only the NEW bars since the last check (no rescanning
         history with refreshed geometry = no backdating), a close
         through the invalidation disarms it, and a detector that
         dissolves without a cross drops it."""
    from analysis.pattern_scan import detect_patterns, TF
    n = len(bars)
    if n < MIN_BARS + 10:
        return []
    break_recent = TF[timeframe]["break_recent"]
    dates = [b["date"] for b in bars]
    active: dict = {}   # pattern -> index its last episode resolved at
    pending: dict = {}  # pattern -> armed forming trigger
    out = []

    def _record(pat, direction, anchor, j, trigger, target, invalid, as_of):
        entry = bars[as_of + 1]["open"] or bars[as_of + 1]["close"]
        if not entry or entry <= 0:
            return
        outcome, bto, w1, b1, rr, retest, end_i = _resolve(
            bars, as_of, entry, target, invalid, direction, trigger)
        active[pat] = end_i
        width = None
        if anchor is not None:
            try:
                width = j - dates.index(anchor)
            except ValueError:
                pass
        out.append((pat, direction, anchor, dates[j], width, trigger,
                    target, invalid, outcome, bto, w1, b1,
                    round(entry, 4), rr, retest,
                    spy_above.get(dates[as_of])))

    for as_of in range(MIN_BARS, n - 1, STEP):
        window = bars[max(0, as_of + 1 - WINDOW):as_of + 1]
        dets = detect_patterns(window, timeframe)
        det_by_pat = {d["pattern"]: d for d in dets}

        # --- path 2: armed forming triggers (checked FIRST so a fresh
        # cross can't be double-entered by path 1 in the same step —
        # active[] then blocks it there).
        for pat, p in list(pending.items()):
            if active.get(pat, -1) >= as_of:
                pending.pop(pat)
                continue
            fired = dead = None
            for j in range(p["last_checked"] + 1, as_of + 1):
                c = bars[j].get("close")
                if c is None:
                    continue
                if p["direction"] == "bullish":
                    if p["invalid"] is not None and c <= p["invalid"]:
                        dead = j
                        break
                    if c > p["trigger"]:
                        fired = j
                        break
                else:
                    if p["invalid"] is not None and c >= p["invalid"]:
                        dead = j
                        break
                    if c < p["trigger"]:
                        fired = j
                        break
            if dead is not None:
                pending.pop(pat)
                continue
            if fired is not None:
                pending.pop(pat)
                _record(pat, p["direction"], p["anchor"], fired,
                        p["trigger"], p["target"], p["invalid"], as_of)
                continue
            p["last_checked"] = as_of
            d = det_by_pat.get(pat)
            if d is None or d["status"] == "breakout":
                pending.pop(pat)          # dissolved without a cross
            else:                          # refresh drifting geometry
                p.update(trigger=d["trigger_price"], target=d["target"],
                         invalid=d["invalid_level"],
                         anchor=d.get("anchor_date"),
                         direction=d["direction"])

        # --- arm new forming triggers
        for pat, d in det_by_pat.items():
            if (d["status"] != "breakout" and pat not in pending
                    and active.get(pat, -1) < as_of):
                pending[pat] = {"trigger": d["trigger_price"],
                                "target": d["target"],
                                "invalid": d["invalid_level"],
                                "direction": d["direction"],
                                "anchor": d.get("anchor_date"),
                                "last_checked": as_of}

        # --- path 1: detector-reported breakouts (unchanged from v2)
        for det in dets:
            if det["status"] != "breakout":
                continue
            j = _breakout_idx(bars, as_of, det["trigger_price"],
                              det["direction"], break_recent)
            # Enter only when the grid FIRST sees the cross (within the
            # last STEP bars) — no excavating breakouts the status filter
            # already had hindsight on.
            if as_of - j >= STEP:
                continue
            pat = det["pattern"]
            if active.get(pat, -1) >= as_of:
                continue    # prior episode still running
            entry = bars[as_of + 1]["open"] or bars[as_of + 1]["close"]
            if not entry or entry <= 0:
                continue
            outcome, bto, w1, b1, rr, retest, end_i = _resolve(
                bars, as_of, entry, det["target"], det["invalid_level"],
                det["direction"], det["trigger_price"])
            active[pat] = end_i
            anchor = det.get("anchor_date")
            width = None
            if anchor is not None:
                try:
                    width = j - dates.index(anchor)
                except ValueError:
                    pass
            out.append((det["pattern"], det["direction"], anchor, dates[j],
                        width, det["trigger_price"], det["target"],
                        det["invalid_level"], outcome, bto, w1, b1,
                        round(entry, 4), rr, retest,
                        spy_above.get(dates[as_of])))
    return out


def _spy_regime_map(conn) -> dict:
    """{trade_date: bool} — SPY close vs its 200-day SMA. None-padded
    implicitly: dates before 200 bars of history just won't be in the map."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT trade_date, close FROM daily_prices
            WHERE ticker = 'SPY' AND trade_date >= %s
            ORDER BY trade_date
        """, (HISTORY_START,))
        rows = cur.fetchall()
    out, window = {}, []
    for d, c in rows:
        window.append(float(c))
        if len(window) > 200:
            window.pop(0)
        if len(window) == 200:
            out[d] = float(c) >= sum(window) / 200.0
    return out


def _stride_sample(universe: list, cap: int) -> list:
    """Deterministic stride over the sorted universe. Pure and pinned:
    ingestion/backfill_daily_history.py (watchtower repo) carries a
    byte-for-byte copy so the deep-history backfill lands under the SAME
    names this replay draws — if this changes, that copy must change with
    it, and test_stride_sample.py exists to make silent drift loud."""
    if len(universe) <= cap:
        return universe
    stride = len(universe) / cap
    return [universe[int(i * stride)] for i in range(cap)]


def run_pattern_backtest(timeframe: str = "daily") -> dict:
    """Replay a deterministic sample of every name daily_prices knows —
    delisted included — and store results incrementally. Truncates the
    table first when it holds rows from an older BT_VERSION, so retired
    measurement rules never blend into the stats."""
    from screen.reversal_screen import _conn
    from analysis.pattern_scan import ENGINE_VERSION
    from psycopg2.extras import execute_values
    conn = _conn()
    t0 = time.time()
    try:
        try:
            with conn.cursor() as _c:
                # 1800s, not 600: the deep window pushed daily_prices to
                # ~14.5M rows and the universe GROUP BY alone crossed 600s
                # under Sunday-night load (2026-08-10, killing the first
                # v6 attempt ten minutes after it truncated v5). This is a
                # background batch job — patience is cheaper than death.
                _c.execute("SET statement_timeout = '1800s'")
            conn.commit()
        except Exception:
            pass
        with conn.cursor() as cur:
            # Truncate on EITHER version bump: measurement rules
            # (BT_VERSION) or detector rules (ENGINE_VERSION). Blending
            # events from different engines was the audit's worst finding
            # — this makes it structurally impossible.
            cur.execute("""
                SELECT 1 FROM pattern_backtest
                WHERE bt_version IS NULL OR bt_version < %s
                   OR engine_version IS NULL OR engine_version < %s
                LIMIT 1
            """, (BT_VERSION, ENGINE_VERSION))
            if cur.fetchone():
                log.info("[patterns] backtest table holds rows from older "
                         "rules (bt v%d / engine v%d current) — truncating "
                         "before re-measure", BT_VERSION, ENGINE_VERSION)
                cur.execute("TRUNCATE pattern_backtest")
        conn.commit()
        with conn.cursor() as cur:
            # Point-in-time universe: anything with enough history,
            # including names that later died — not today's screen.
            cur.execute("""
                SELECT ticker FROM daily_prices
                WHERE trade_date >= %s
                GROUP BY ticker HAVING count(*) >= %s
            """, (HISTORY_START, MIN_BARS + 10))
            universe = sorted({r[0] for r in cur.fetchall() if r[0]})
        universe = _stride_sample(universe, SAMPLE)
        with conn.cursor() as cur:
            # Resume: a deploy mid-run restarts this thread; skip names the
            # interrupted run already stored (eventless names re-scan — a
            # little waste beats a silently unscanned tail).
            cur.execute("SELECT DISTINCT ticker FROM pattern_backtest "
                        "WHERE bt_version = %s AND engine_version = %s "
                        "AND timeframe = %s",
                        (BT_VERSION, ENGINE_VERSION, timeframe))
            done = {r[0] for r in cur.fetchall()}
        if done:
            before = len(universe)
            universe = [t for t in universe if t not in done]
            log.info(f"[patterns] backtest resuming: {before - len(universe)} "
                     f"names already stored, {len(universe)} to go")
        spy_above = _spy_regime_map(conn)
        log.info(f"[patterns] backtest v{BT_VERSION} {timeframe} replay over "
                 f"{len(universe)} names (step {STEP} bars, "
                 f"engine v{ENGINE_VERSION})")
        total = 0
        failed = 0
        for i in range(0, len(universe), 120):
            frames = _bars_for(conn, universe[i:i + 120])
            if timeframe == "weekly":
                frames = {t: _weekly_bars(bs) for t, bs in frames.items()}
            rows = []
            for t, bars in frames.items():
                try:
                    for ev in _replay_ticker(bars, spy_above, timeframe):
                        rows.append((t,) + ev
                                    + (timeframe, ENGINE_VERSION, BT_VERSION))
                except Exception as e:
                    # A ticker that dies here silently CENSORS the sample —
                    # v4/v5 lost most deep names this way and nobody saw it
                    # (log.debug is invisible at INFO). Loud, counted, and
                    # gating the completion marker below.
                    failed += 1
                    if failed <= 5:
                        log.warning(f"[patterns] backtest {t} replay FAILED: {e!r}")
            if rows:
                with conn.cursor() as cur:
                    execute_values(cur, """
                        INSERT INTO pattern_backtest
                            (ticker, pattern, direction, anchor_date,
                             breakout_date, base_width_bars, trigger_price,
                             target, invalid_level, outcome, bars_to_outcome,
                             win_1r, bars_to_1r, entry_price, realized_r,
                             retest_bar, spy_above, timeframe,
                             engine_version, bt_version)
                        VALUES %s
                        ON CONFLICT (ticker, timeframe, pattern, breakout_date)
                        DO UPDATE SET
                            outcome = EXCLUDED.outcome,
                            bars_to_outcome = EXCLUDED.bars_to_outcome,
                            win_1r = EXCLUDED.win_1r,
                            bars_to_1r = EXCLUDED.bars_to_1r,
                            entry_price = EXCLUDED.entry_price,
                            realized_r = EXCLUDED.realized_r,
                            retest_bar = EXCLUDED.retest_bar,
                            spy_above = EXCLUDED.spy_above,
                            engine_version = EXCLUDED.engine_version,
                            bt_version = EXCLUDED.bt_version
                    """, rows, page_size=2000)
                conn.commit()   # incremental — an interrupted run keeps progress
                total += len(rows)
            if i % 600 == 0 and i:
                log.info(f"[patterns] backtest {i}/{len(universe)} names, "
                         f"{total} breakouts so far")
        if failed:
            log.warning(f"[patterns] backtest {timeframe}: {failed} of "
                        f"{len(universe)} tickers failed replay")
        if failed > max(10, len(universe) // 50):
            # A run that lost >2% of its universe is not complete — it is a
            # censored sample. No marker: the seed refires next start and
            # the resume machinery retries exactly the failed names.
            log.warning(f"[patterns] backtest {timeframe}: failure rate too "
                        "high — completion marker WITHHELD; will retry on "
                        "next service start")
            return {"breakouts": total, "tickers": len(universe),
                    "failed": failed, "bt_version": BT_VERSION,
                    "incomplete": True,
                    "seconds": round(time.time() - t0)}
        with conn.cursor() as cur:
            # Permanent completion marker — the seed re-fires until this
            # exists, so an interrupted run always resumes on next deploy.
            cur.execute("""
                INSERT INTO scheduler_job_claims (job_name, run_date)
                VALUES (%s, CURRENT_DATE)
                ON CONFLICT (job_name, run_date) DO NOTHING
            """, (f"pattern_bt_v{BT_VERSION}_e{ENGINE_VERSION}_complete",))
        conn.commit()
        log.info(f"[patterns] backtest v{BT_VERSION} stored {total} breakouts "
                 f"in {time.time() - t0:.0f}s")
        return {"breakouts": total, "tickers": len(universe),
                "bt_version": BT_VERSION,
                "seconds": round(time.time() - t0)}
    finally:
        conn.close()


def run_pattern_backtests() -> dict:
    """Replay every configured timeframe (PATTERN_BT_TIMEFRAMES, default
    daily,weekly). The scheduler seed and the rerun tool both call this so a
    version bump rebuilds all timeframes, not just daily."""
    out = {}
    for tf in BACKTEST_TIMEFRAMES:
        out[tf] = run_pattern_backtest(tf)
    return out


def timing_stats(conn=None) -> dict:
    """Per-pattern timing: {pattern: {n, hit_rate, p25, med, p75, r1_med,
    r1_p75}} in BARS. p25/med/p75 = breakout to full target-touch (winners
    only); r1_med/r1_p75 = breakout to FIRST TRIM (+1R touch) on rows that
    got there — the number that sizes a swing-leg option for a trim-into-
    strength style rather than a hold-to-target one."""
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
                           FILTER (WHERE outcome='target'),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY bars_to_1r)
                           FILTER (WHERE win_1r),
                       percentile_cont(0.75) WITHIN GROUP (ORDER BY bars_to_1r)
                           FILTER (WHERE win_1r)
                FROM pattern_backtest
                WHERE timeframe = 'daily'
                GROUP BY pattern
            """)
            out = {}
            for p, n, hr, p25, med, p75, r1m, r1p in cur.fetchall():
                out[p] = {"n": int(n),
                          "hit_rate": float(hr) if hr is not None else None,
                          "p25": float(p25) if p25 is not None else None,
                          "med": float(med) if med is not None else None,
                          "p75": float(p75) if p75 is not None else None,
                          "r1_med": float(r1m) if r1m is not None else None,
                          "r1_p75": float(r1p) if r1p is not None else None}
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


def estimate_trim(pattern: str, timeframe: str, stats: dict) -> dict:
    """{'weeks_hi', 'dte', 'source'} — expiry sizing for the SWING leg of a
    trim-into-strength trade: monetize the first explosive leg (+1R), not
    the full pattern resolution. Gamma trades want short-dated contracts,
    but not melting ones, so: DTE = 3.5x the MEDIAN measured time-to-pop
    (cushion for one failed first attempt plus a retry), also covering
    1.5x the slow-quartile pop, snapped to tenors with a 45-DTE floor —
    theta over a 2-3 week hold is modest at 60 DTE and brutal under 30.
    Empty dict when the pattern has no measured +1R sample — callers fall
    back to a fraction of the runner DTE."""
    bpw = BARS_PER_WEEK.get(timeframe, 5.0)
    st = (stats or {}).get(pattern) or {}
    med, p75 = st.get("r1_med"), st.get("r1_p75")
    if st.get("n", 0) < 30 or med is None:
        return {}
    hi = max(round((p75 or med) / bpw, 1), 0.5)
    want = max((med / bpw) * 7 * 3.5,
               hi * 7 * 1.5, 45)
    dte = next((t for t in DTE_TENORS if t >= want), DTE_TENORS[-1])
    return {"weeks_hi": hi, "dte": dte, "source": "measured"}
