"""
The 16D green-dot screen (2026-08-29, Eric: "build the screen" — the
study graded his claim the night before: below-zero 16D dots on
drawdown stocks beat their cohort by ~15 points of median 6-mo return;
deep crosses (≤ −30) carry the signal; the dot forecasts the bottom's
ERA, not its tick — median path dips another ~20% first).

Mechanics:
  - Dots can only print when a fixed-anchor 16-day block COMPLETES,
    and blocks complete for the whole fleet on the same calendar day
    (the anchor is SPY's global trading-day index). The nightly job
    detects a newly completed block (claim per block id) and runs an
    incremental fleet pass — greendot_study._process_ticker is
    idempotent (ON CONFLICT DO NOTHING), so only new dots append.
  - Forward outcomes on accumulating dots fill in as history arrives
    (update_forward_outcomes) — the screen's own alert-performance.
  - The actionable screen: fresh dots (≤ 2 blocks old) with real
    drawdown and a deep cross, served via watchtower_greendot with the
    study's priors and the survivorship caveat stamped in the render.
  - 16d also becomes an exemplar-museum timeframe: state_16d() computes
    the full component row from the same fixed-anchor bars so Eric's
    reads on these charts are captured with real state.
"""
import datetime as dt
import logging

log = logging.getLogger("watchtower.greendot_screen")

BLOCK = 16
FRESH_BLOCKS = 2
SCREEN_MIN_DD = 30.0      # percent, b30_50 and deeper
DEEP_CROSS = -30.0

PRIORS = ("study priors (20,404 dots, listed-universe — survivors only, "
          "real-world runs below these): deep-cross dots on 50%+ drawdown "
          "grade +5.8 to +10.0% median 6-mo vs −5 to −11% for random "
          "cohort days, 56-57% positive; median path dips another 17-25% "
          "below the dot first; only ~30% see a lower dot within a year")


def last_complete_block(n_cal_days: int) -> int:
    """Pure. The last block KNOWN complete: a block is complete only
    once a day of the NEXT block exists (the study's partial-block drop,
    restated). Returns -1 when none."""
    if n_cal_days < BLOCK + 1:
        return -1
    return (n_cal_days - 1) // BLOCK - 1


def maybe_append_new_dots() -> dict:
    """Nightly: if a new 16D block completed, re-run the incremental
    fleet pass (new dots only) and claim the block id."""
    from analysis.greendot_study import _process_ticker
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT count(*) FROM daily_prices WHERE ticker='SPY'")
            n = c.fetchone()[0]
        bid = last_complete_block(n)
        if bid < 0:
            return {"block": None}
        claim = f"greendot_block_{bid}"
        with conn.cursor() as c:
            c.execute("""INSERT INTO scheduler_job_claims (job_name, run_date)
                         VALUES (%s, CURRENT_DATE) ON CONFLICT DO NOTHING
                         RETURNING job_name""", (claim,))
            fresh = c.fetchone() is not None
        conn.commit()
        if not fresh:
            return {"block": bid, "new": False}
        with conn.cursor() as c:
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT ticker FROM tickers
                         WHERE COALESCE(delisted,false)=false ORDER BY ticker""")
            fleet = [r[0] for r in c.fetchall()]
        added = 0
        for tk in fleet:
            try:
                added += _process_ticker(conn, tk, cal)
            except Exception as e:
                conn.rollback()
                log.warning("[greendot-screen] %s: %s", tk, str(e)[:200])
        log.info("[greendot-screen] block %d complete — %d new dot(s).",
                 bid, added)
        return {"block": bid, "new": True, "dots": added}
    finally:
        conn.close()


def update_forward_outcomes() -> int:
    """Fill fwd fields on dots whose future has arrived. The screen's
    forward-return grading accrues here, one horizon at a time."""
    from screen.reversal_screen import _conn
    conn = _conn()
    n = 0
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, ticker, dot_date, px_at_dot
                         FROM greendot_dots
                         WHERE fwd_252d_pct IS NULL
                           AND dot_date < CURRENT_DATE - 60""")
            todo = c.fetchall()
        for did, tk, d0, px in todo:
            px = float(px)
            with conn.cursor() as c:
                c.execute("""SELECT trade_date, close FROM daily_prices
                             WHERE ticker=%s AND trade_date > %s
                             ORDER BY trade_date""", (tk, d0))
                rows = c.fetchall()
            if not rows:
                continue
            closes = [float(r[1]) for r in rows]
            def pct(i):
                return round((closes[i] / px - 1) * 100, 2) if len(closes) > i else None
            low6 = min(closes[:126]) if closes else None
            with conn.cursor() as c:
                c.execute("""UPDATE greendot_dots SET
                               fwd_63d_pct  = COALESCE(fwd_63d_pct, %s),
                               fwd_126d_pct = COALESCE(fwd_126d_pct, %s),
                               fwd_252d_pct = COALESCE(fwd_252d_pct, %s),
                               fwd_low_6m   = COALESCE(fwd_low_6m, %s),
                               dist_to_low_pct = COALESCE(dist_to_low_pct, %s)
                             WHERE id=%s""",
                          (pct(62), pct(125), pct(251), low6,
                           round((low6 / px - 1) * 100, 2) if low6 else None,
                           did))
            n += 1
        conn.commit()
    finally:
        conn.close()
    if n:
        log.info("[greendot-screen] forward outcomes updated on %d dot(s).", n)
    return n


def format_screen(rows, spy_days: int) -> str:
    if not rows:
        return ("16D GREEN-DOT SCREEN — no fresh qualifying dots "
                f"(deep cross ≤ {DEEP_CROSS:g}, drawdown ≥ {SCREEN_MIN_DD:g}%, "
                f"≤ {FRESH_BLOCKS} blocks old). A quiet screen is a reading, "
                "not an error.\n" + PRIORS)
    lines = [f"16D GREEN-DOT SCREEN — {len(rows)} fresh dot(s) "
             f"(below-zero cross, drawdown ≥ {SCREEN_MIN_DD:g}%):"]
    for (tk, d0, depth, dd, px, cur) in rows:
        chg = f"{(float(cur)/float(px)-1)*100:+.1f}% since dot" if cur else "px n/a"
        lines.append(f"  {tk:<6} dot {d0} · cross {float(depth):+.1f} · "
                     f"drawdown {float(dd):.0f}% · at {float(px):g} · {chg}")
    lines.append(PRIORS)
    lines.append("Doctrine: the dot forecasts the bottom's ERA — expect "
                 "adverse excursion; size for tranches, never a lump.")
    return "\n".join(lines)


def screen_rows():
    """Fresh actionable dots joined to current price."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT count(*) FROM daily_prices WHERE ticker='SPY'")
            n = c.fetchone()[0]
            # Fresh = within the last FRESH_BLOCKS completed blocks —
            # approximated in calendar terms (16 trading days ≈ 23 cal).
            c.execute("""
                SELECT g.ticker, g.dot_date, g.cross_depth, g.drawdown_pct,
                       g.px_at_dot,
                       (SELECT close FROM daily_prices d
                        WHERE d.ticker=g.ticker ORDER BY trade_date DESC
                        LIMIT 1) AS cur
                FROM greendot_dots g
                WHERE g.dot_date >= CURRENT_DATE - %s
                  AND g.cross_depth <= %s AND g.drawdown_pct >= %s
                ORDER BY g.drawdown_pct DESC, g.cross_depth ASC""",
                (int(FRESH_BLOCKS * 23), DEEP_CROSS, SCREEN_MIN_DD))
            rows = c.fetchall()
        return rows, n
    finally:
        conn.close()


def state_16d(ticker: str):
    """Full oscillator component row at the last COMPLETED fixed-anchor
    16D bar — the exemplar museum's 16d capture source. Returns
    (state_dict, bar_end_date) or (None, reason)."""
    import pandas as pd
    from analysis.oscillator import compute_oscillator
    from screen.reversal_screen import _conn
    from analysis.greendot_study import blocks_16d
    conn = _conn()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT trade_date FROM daily_prices
                         WHERE ticker='SPY' ORDER BY trade_date""")
            cal = {r[0]: i for i, r in enumerate(c.fetchall())}
            c.execute("""SELECT trade_date, COALESCE(open, close),
                                COALESCE(high, close), COALESCE(low, close),
                                close, COALESCE(volume, 0)
                         FROM daily_prices
                         WHERE ticker=%s AND close IS NOT NULL
                         ORDER BY trade_date""", (ticker,))
            rows = c.fetchall()
    finally:
        conn.close()
    if len(rows) < 200:
        return None, f"only {len(rows)} daily bars for {ticker} — too thin for 16D"
    dates = [r[0] for r in rows]
    blk = blocks_16d(dates, cal)
    bars, cur, cur_id = [], None, None
    for i, r in enumerate(rows):
        if blk[i] != cur_id:
            if cur is not None:
                bars.append(cur)
            cur_id = blk[i]
            cur = dict(end=dates[i], o=float(r[1]), h=float(r[2]),
                       l=float(r[3]), c=float(r[4]), v=float(r[5]))
        else:
            cur.update(h=max(cur["h"], float(r[2])),
                       l=min(cur["l"], float(r[3])), c=float(r[4]),
                       v=cur["v"] + float(r[5]), end=dates[i])
    if len(bars) < 40:
        return None, "not enough complete 16D bars"
    df = pd.DataFrame({k: [b[x] for b in bars] for k, x in
                       (("open", "o"), ("high", "h"), ("low", "l"),
                        ("close", "c"), ("volume", "v"))},
                      index=pd.DatetimeIndex([pd.Timestamp(b["end"]) for b in bars]))
    odf = compute_oscillator(df)
    last = odf.iloc[-1]
    import math
    def f(v):
        try:
            v = float(v)
            return None if math.isnan(v) else round(v, 4)
        except (TypeError, ValueError):
            return None
    keys = ("wt1", "wt2", "wt_diff", "mf_candle", "mf_volume", "rsi",
            "stoch_k", "stoch_d", "pctr", "pctr_ema", "macd",
            "macd_signal", "macd_hist")
    state = {k: f(last.get(k)) for k in keys}
    state["timeframe"] = "16d"
    state["bar_end"] = str(bars[-1]["end"])
    state["close"] = bars[-1]["c"]
    return state, str(bars[-1]["end"])
