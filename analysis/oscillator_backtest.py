"""
Watchtower Oscillator — historical backtest of the entry signals.

Because every signal is computed on CONFIRMED bars with zero repaint, a
historical replay is honest by construction: the signal this backtest sees
on a 2024 bar is exactly what the live scanner would have fired that
evening. No survivorship of indicator state, no lookahead.

What it replays (vectorized over the full daily history in daily_prices):
  wt_extreme_cross — WaveTrend cross while wt2 is beyond the ±53 band
  pctr_hook        — %R(28) exits the extreme band after 3+ pinned closes

For every event it records the local oscillator state plus the ENTRY-GRADE
QUALITY GATES so their value can be measured instead of assumed:
  weekly_ok — the weekly wave (wt_diff) was rising as of that bar's close
  rs_pct    — cross-sectional 63-day-return percentile on the event date
  vs200     — distance from the 200-session SMA
  gates_passed — weekly_ok AND rs floor (>=25 bullish / <=75 bearish)

Outcomes: forward 5/10/21/63-session returns, plus SPY's same-window
return for excess. (The live entry-grade view also demands a chart pattern
near its trigger; replaying pattern detection historically is future work,
so the backtest measures the oscillator + weekly + RS layers.)

Results land in oscillator_backtest (migration 0067), one row per
(ticker, event_date, signal_type) — rerunnable, idempotent. Runs once
automatically at deploy when the table is empty (same seeding pattern as
the scans); ~5,900 tickers takes a few minutes.
"""
import logging
import time

import numpy as np
import pandas as pd

from analysis.oscillator import (compute_oscillator, resample_weekly,
                                 WT_INNER, _fetch_daily_ohlcv)

log = logging.getLogger(__name__)

FWD = (5, 10, 21, 63)
BATCH = 120
MIN_BARS = 260


def _events_for_ticker(daily: pd.DataFrame) -> pd.DataFrame:
    """All backtestable events for one ticker, with local state and forward
    returns. Everything here is causal: each column at bar i uses bars <= i,
    except the fwd_* columns which are the measured outcome."""
    o = compute_oscillator(daily)
    close = o["close"]

    # Weekly wave slope, known as-of each daily close. A weekly bar completes
    # at its week's last session close — mapping it onto that same close (and
    # forward-filling until the next completed week) never leaks the future.
    wk = resample_weekly(daily, drop_partial=False)
    if len(wk) >= 40:
        wko = compute_oscillator(wk)
        weekly_rising = wko["wt_diff"].reindex(o.index, method="ffill") > 0
    else:
        weekly_rising = pd.Series(False, index=o.index)

    vs200 = close / close.rolling(200).mean() - 1
    ret63 = close.pct_change(63)

    up = (o["wt1"] > o["wt2"]) & (o["wt1"].shift(1) <= o["wt2"].shift(1))
    dn = (o["wt1"] < o["wt2"]) & (o["wt1"].shift(1) >= o["wt2"].shift(1))
    x_bull = up & (o["wt2"] <= -WT_INNER)
    x_bear = dn & (o["wt2"] >= WT_INNER)

    pinned3 = lambda s, thr, below: (
        ((s.shift(1) <= thr) & (s.shift(2) <= thr) & (s.shift(3) <= thr))
        if below else
        ((s.shift(1) >= thr) & (s.shift(2) >= thr) & (s.shift(3) >= thr)))
    h_bull = (o["pctr"] > -80) & pinned3(o["pctr"], -80, True)
    h_bear = (o["pctr"] < -20) & pinned3(o["pctr"], -20, False)

    # Coil: 10 tight bars while the wave bleeds >=15 points (bearish mirror:
    # wave RISES 15+ into flat price). First bar of each episode only — a
    # coil is a state, not a per-bar event. (The live signal additionally
    # requires the pivot-low shelf to hold; the replay approximates without
    # it.) The rsi/rsi_hold columns split these into Eric's "power coils" —
    # wave resetting while RSI holds its trend range (Cardwell range rules).
    band_hi = close.rolling(10).max()
    band_lo = close.rolling(10).min()
    tight = (band_lo > 0) & (band_hi / band_lo - 1 <= 0.04)
    bleed = o["wt1"].shift(9) - o["wt1"]
    c_bull = tight & (bleed >= 15)
    c_bear = tight & (bleed <= -15)
    c_bull = c_bull & ~c_bull.shift(1).fillna(False).astype(bool)
    c_bear = c_bear & ~c_bear.shift(1).fillna(False).astype(bool)

    # Money-flow rounding: 5th consecutive bar in the turn direction after a
    # meaningful swing, with the flow moving the other way before the turn —
    # one event per arc (the streak==5 bar), the vectorized cousin of the
    # live mf_round signal.
    mf = o["mf_candle"]
    inc = mf.diff() > 0
    up_streak = inc.groupby((~inc).cumsum()).cumsum()
    dec = mf.diff() < 0
    dn_streak = dec.groupby((~dec).cumsum()).cumsum()
    r_bull = (up_streak == 5) & (mf - mf.shift(5) >= 3.0) \
        & (mf.shift(5) <= mf.shift(10))
    r_bear = (dn_streak == 5) & (mf.shift(5) - mf >= 3.0) \
        & (mf.shift(5) >= mf.shift(10))

    # STACKED divergences on confirmed pivots (k=3 both sides — the signal
    # becomes visible 3 bars after the second pivot, so the event is stamped
    # there; no lookahead). Same two price pivots checked against four
    # series — wave / RSI / money flow / MACD histogram — div_count records
    # how many agreed, so the report can compare 1x vs 2x+ divergences.
    from analysis.oscillator import _pivot_idx
    lows_v = daily["low"].values
    highs_v = daily["high"].values
    n = len(o)
    DIV = (("wave", o["wt1"].values, 0.0), ("rsi", o["rsi"].values, 50.0),
           ("mf", o["mf_candle"].values, 0.0),
           ("macd", o["macd_hist"].values, 0.0))

    def _hits(a, b, bearish):
        out = []
        for nm, arr, mid in DIV:
            va, vb = arr[a], arr[b]
            if np.isnan(va) or np.isnan(vb):
                continue
            if (bearish and va > mid and vb < va) or \
                    (not bearish and va < mid and vb > va):
                out.append(nm)
        return out

    d_bull = pd.Series(False, index=o.index)
    d_bear = pd.Series(False, index=o.index)
    div_meta: dict = {}
    for a, b in zip(*(lambda p: (p, p[1:]))(_pivot_idx(lows_v, 3, "low"))):
        if b - a <= 60 and b + 3 < n and lows_v[b] < lows_v[a]:
            hits = _hits(a, b, bearish=False)
            if hits:
                d_bull.iloc[b + 3] = True
                div_meta[o.index[b + 3]] = (len(hits), "+".join(hits))
    for a, b in zip(*(lambda p: (p, p[1:]))(_pivot_idx(highs_v, 3, "high"))):
        if b - a <= 60 and b + 3 < n and highs_v[b] > highs_v[a]:
            hits = _hits(a, b, bearish=True)
            if hits:
                d_bear.iloc[b + 3] = True
                div_meta[o.index[b + 3]] = (len(hits), "+".join(hits))

    frames = []
    for mask, sig, direction in ((x_bull, "wt_extreme_cross", "bullish"),
                                 (x_bear, "wt_extreme_cross", "bearish"),
                                 (h_bull, "pctr_hook", "bullish"),
                                 (h_bear, "pctr_hook", "bearish"),
                                 (c_bull, "coil", "bullish"),
                                 (c_bear, "coil", "bearish"),
                                 (r_bull, "mf_round", "bullish"),
                                 (r_bear, "mf_round", "bearish"),
                                 (d_bull, "divergence", "bullish"),
                                 (d_bear, "divergence", "bearish")):
        idx = o.index[mask.fillna(False)]
        if not len(idx):
            continue
        ev = pd.DataFrame(index=idx)
        ev["signal_type"] = sig
        ev["direction"] = direction
        ev["close"] = close.loc[idx]
        ev["wt2"] = o["wt2"].loc[idx]
        ev["mf"] = o["mf_candle"].loc[idx]
        ev["pctr"] = o["pctr"].loc[idx]
        ev["macd_ok"] = (np.sign(o["macd_hist"].loc[idx])
                         == (1 if direction == "bullish" else -1))
        ev["rsi"] = o["rsi"].loc[idx]
        # Range-rule agreement: RSI holding its trend-side of 50 at the event
        ev["rsi_hold"] = (ev["rsi"] >= 50) if direction == "bullish" \
            else (ev["rsi"] <= 50)
        if sig == "divergence":
            ev["div_count"] = [div_meta.get(i, (None, None))[0] for i in idx]
            ev["div_inds"] = [div_meta.get(i, (None, None))[1] for i in idx]
        else:
            ev["div_count"] = None
            ev["div_inds"] = None
        ev["weekly_ok"] = (weekly_rising if direction == "bullish"
                           else ~weekly_rising).loc[idx]
        ev["vs200"] = vs200.loc[idx]
        ev["ret63"] = ret63.loc[idx]
        for k in FWD:
            ev[f"fwd{k}"] = (close.shift(-k) / close - 1).loc[idx]
        frames.append(ev)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames)
    out.index.name = "event_date"
    return out.reset_index()


def run_backtest() -> dict:
    """Replay the full universe and store events + outcomes. Idempotent —
    re-running upserts the same (ticker, event_date, signal_type) keys."""
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
            tickers = sorted({r[0] for r in cur.fetchall() if r[0]})
        log.info(f"[oscillator] backtest over {len(tickers)} names")

        all_events = []
        ret63_cols = {}
        for i in range(0, len(tickers), BATCH):
            frames = _fetch_daily_ohlcv(conn, tickers[i:i + BATCH], days=1200)
            for t, daily in frames.items():
                if len(daily) < MIN_BARS:
                    continue
                try:
                    ev = _events_for_ticker(daily)
                    if len(ev):
                        ev.insert(0, "ticker", t)
                        all_events.append(ev)
                    ret63_cols[t] = (daily["close"].pct_change(63))
                except Exception as e:
                    log.debug(f"[oscillator] backtest {t} failed: {e}")
        if not all_events:
            log.warning("[oscillator] backtest found no events")
            return {"events": 0}
        events = pd.concat(all_events, ignore_index=True)

        # Cross-sectional RS percentile per date — ranks each event's 63-day
        # return against every name trading that day, like the live screener.
        ret63 = pd.DataFrame(ret63_cols)
        rs = ret63.rank(axis=1, pct=True) * 99
        events["rs_pct"] = [
            rs.at[d, t] if (t in rs.columns and d in rs.index) else np.nan
            for d, t in zip(events["event_date"], events["ticker"])]
        bull = events["direction"] == "bullish"
        rs_ok = pd.Series(np.where(
            bull, events["rs_pct"].fillna(0) >= 25,
            events["rs_pct"].fillna(100) <= 75), index=events.index)
        events["gates_passed"] = events["weekly_ok"].fillna(False) & rs_ok

        # SPY same-window forward returns for excess-return measurement
        spy = _fetch_daily_ohlcv(conn, ["SPY"], days=1200).get("SPY")
        if spy is not None and len(spy):
            sc = spy["close"]
            for k in FWD:
                fwd = (sc.shift(-k) / sc - 1)
                events[f"spy_fwd{k}"] = events["event_date"].map(fwd)
        else:
            for k in FWD:
                events[f"spy_fwd{k}"] = np.nan

        def f(v):
            try:
                v = float(v)
                return None if (np.isnan(v) or np.isinf(v)) else round(v, 5)
            except Exception:
                return None
        rows = [(r.ticker, r.event_date, r.signal_type, r.direction,
                 f(r.close), f(r.wt2), f(r.mf), f(r.pctr),
                 bool(r.macd_ok), bool(r.weekly_ok) if pd.notna(r.weekly_ok) else False,
                 f(r.rs_pct), f(r.vs200), bool(r.gates_passed),
                 f(r.fwd5), f(r.fwd10), f(r.fwd21), f(r.fwd63),
                 f(r.spy_fwd21), f(r.rsi),
                 bool(r.rsi_hold) if pd.notna(r.rsi_hold) else None,
                 int(r.div_count) if pd.notna(r.div_count) else None,
                 r.div_inds if isinstance(r.div_inds, str) else None)
                for r in events.itertuples()]
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO oscillator_backtest
                    (ticker, event_date, signal_type, direction, close, wt2,
                     mf, pctr, macd_ok, weekly_ok, rs_pct, vs200,
                     gates_passed, fwd5, fwd10, fwd21, fwd63, spy_fwd21,
                     rsi, rsi_hold, div_count, div_indicators)
                VALUES %s
                ON CONFLICT (ticker, event_date, signal_type) DO UPDATE SET
                    fwd5=EXCLUDED.fwd5, fwd10=EXCLUDED.fwd10,
                    fwd21=EXCLUDED.fwd21, fwd63=EXCLUDED.fwd63,
                    spy_fwd21=EXCLUDED.spy_fwd21,
                    gates_passed=EXCLUDED.gates_passed,
                    rs_pct=EXCLUDED.rs_pct,
                    rsi=EXCLUDED.rsi, rsi_hold=EXCLUDED.rsi_hold,
                    div_count=EXCLUDED.div_count,
                    div_indicators=EXCLUDED.div_indicators
            """, rows, page_size=2000)
        conn.commit()
        n = len(rows)
        log.info(f"[oscillator] backtest stored {n} events "
                 f"in {time.time() - t0:.0f}s")
        return {"events": n, "tickers": len(ret63_cols),
                "seconds": round(time.time() - t0)}
    finally:
        conn.close()


def backtest_report() -> dict:
    """Aggregate the stored backtest: per signal/direction, gated vs raw —
    sample size, 21-session win rate, average/median forward returns, and
    average excess vs SPY."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT signal_type, direction, gates_passed, count(*) AS n,
                       round(avg(CASE WHEN direction='bullish' THEN fwd21
                                      ELSE -fwd21 END) * 100, 2),
                       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY
                             CASE WHEN direction='bullish' THEN fwd21
                                  ELSE -fwd21 END)::numeric * 100, 2),
                       round(avg(CASE WHEN (direction='bullish' AND fwd21 > 0)
                                      OR (direction='bearish' AND fwd21 < 0)
                                 THEN 1.0 ELSE 0.0 END) * 100, 1),
                       round(avg(CASE WHEN direction='bullish'
                                      THEN fwd21 - spy_fwd21
                                      ELSE spy_fwd21 - fwd21 END) * 100, 2),
                       round(avg(CASE WHEN direction='bullish' THEN fwd63
                                      ELSE -fwd63 END) * 100, 2)
                FROM oscillator_backtest
                WHERE fwd21 IS NOT NULL
                GROUP BY signal_type, direction, gates_passed
                ORDER BY signal_type, direction, gates_passed
            """)
            rows = cur.fetchall()
            cur.execute("SELECT min(event_date), max(event_date), count(*) "
                        "FROM oscillator_backtest")
            lo, hi, total = cur.fetchone()
    finally:
        conn.close()
    return {
        "window": [str(lo), str(hi)], "total_events": int(total or 0),
        "rows": [{
            "signal_type": r[0], "direction": r[1], "gated": bool(r[2]),
            "n": int(r[3]),
            "avg_fwd21_pct": float(r[4]) if r[4] is not None else None,
            "med_fwd21_pct": float(r[5]) if r[5] is not None else None,
            "win_rate_21d_pct": float(r[6]) if r[6] is not None else None,
            "avg_excess21_vs_spy_pct": float(r[7]) if r[7] is not None else None,
            "avg_fwd63_pct": float(r[8]) if r[8] is not None else None,
        } for r in rows],
    }
