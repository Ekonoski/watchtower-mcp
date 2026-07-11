"""
Watchtower — the Watchtower Oscillator.

A composite momentum/flow engine built from public-domain math: LazyBear's
WaveTrend (the wave pair), dual money-flow reads, RSI/StochRSI, Williams %R
with an EMA guide, and MACD as a confirmation layer. Everything is computed
on CONFIRMED bars only — the live, still-forming bar is never used, so a
signal that exists at bar close can never repaint away later.

Timeframes: daily and weekly (resampled) from daily_prices, 4h from Polygon
(same bounded candidate set the pattern scan uses), plus 2-day/3-day
resamples of the daily series (computed on demand for single-ticker reads).
Multi-day groups are anchored at the END of the series so the newest group
always contains exactly N sessions — complete by construction. (TradingView
anchors multi-day bars at the start of its history, so a 2D/3D panel there
can be phase-shifted by one session vs ours; wave shape and levels match,
individual cross bars may differ by one.)

Outputs land in two tables (migration 0065):
  oscillator_scan     — current snapshot per (ticker, timeframe), upserted
  oscillator_signals  — append-only history of fired signals, structured to
                        feed the alert-performance pipeline (7/30/90-day
                        forward returns) later.

Indicator settings are fixed to Eric's TradingView panels: WaveTrend
10/21/4 with ±53/±60 bands, %R length 28 with EMA 21, MACD 12/26/9,
RSI 14 and StochRSI 14/14/3/3.
"""
import json
import logging
import time

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# WaveTrend bands (Cipher-style): inner = actionable extreme, outer = blown out.
WT_INNER = 53.0
WT_OUTER = 60.0

# Default money-flow variant shown in summaries. Both are always computed and
# stored; validation against the TradingView fill picks which one leads.
MF_DEFAULT = "mf_candle"

TIMEFRAMES = ("daily", "weekly", "4h")          # scanned + stored
RESAMPLE_TFS = ("2d", "3d")                     # on-demand, single-ticker reads


# ── Math helpers (vectorized) ────────────────────────────────────────────────

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def compute_oscillator(df: pd.DataFrame) -> pd.DataFrame:
    """All indicator columns for a confirmed-bars OHLCV frame.

    df: columns open, high, low, close, volume; ascending index of bar
    timestamps. Returns df with indicator columns appended (same index).
    """
    out = df.copy()
    rng = (out["high"] - out["low"]).replace(0, np.nan)   # doji guard

    # WaveTrend (LazyBear): hlc3 → esa/d → ci → wt1/wt2
    ap = (out["high"] + out["low"] + out["close"]) / 3.0
    esa = _ema(ap, 10)
    d = _ema((ap - esa).abs(), 10)
    ci = (ap - esa) / (0.015 * d.replace(0, np.nan))
    out["wt1"] = _ema(ci, 21)
    out["wt2"] = _sma(out["wt1"], 4)
    out["wt_diff"] = out["wt1"] - out["wt2"]

    # Money flow, candle-position style (visual match to the Cipher fill)
    out["mf_candle"] = _sma(((out["close"] - out["open"]) / rng).fillna(0.0) * 150.0, 60)

    # Money flow, volume-weighted close-location (true flow-of-money read)
    clv = (((out["close"] - out["low"]) - (out["high"] - out["close"])) / rng).fillna(0.0)
    vol = out["volume"].fillna(0.0)
    out["mf_volume"] = _ema(clv * vol, 21) / _ema(vol, 21).replace(0, np.nan)

    # RSI(14), Wilder smoothing
    delta = out["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = (100 - 100 / (1 + rs)).fillna(100.0)

    # StochRSI 14/14, %K smooth 3, %D 3
    rmin = out["rsi"].rolling(14).min()
    rmax = out["rsi"].rolling(14).max()
    stoch = (out["rsi"] - rmin) / (rmax - rmin).replace(0, np.nan) * 100.0
    out["stoch_k"] = _sma(stoch, 3)
    out["stoch_d"] = _sma(out["stoch_k"], 3)

    # Williams %R length 28 (+ EMA 21 guide) — Eric's TradingView settings
    hh = out["high"].rolling(28).max()
    ll = out["low"].rolling(28).min()
    out["pctr"] = (out["close"] - hh) / (hh - ll).replace(0, np.nan) * 100.0
    out["pctr_ema"] = _ema(out["pctr"], 21)

    # MACD 12/26/9
    macd = _ema(out["close"], 12) - _ema(out["close"], 26)
    out["macd"] = macd
    out["macd_signal"] = _ema(macd, 9)
    out["macd_hist"] = macd - out["macd_signal"]

    return out


# ── Bar assembly (confirmed bars only) ───────────────────────────────────────

def resample_days(daily: pd.DataFrame, k: int) -> pd.DataFrame:
    """k-session bars anchored at the END of the series: the newest group has
    exactly k sessions, so the last bar is complete by construction. A
    leftover partial group at the START is dropped."""
    n = len(daily)
    if n < k:
        return daily.iloc[0:0]
    start = n % k
    d = daily.iloc[start:]
    grp = np.arange(len(d)) // k
    agg = d.groupby(grp).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))
    agg.index = d.index[(np.arange(len(d)) % k) == k - 1]  # stamp = group's last session
    return agg


def resample_weekly(daily: pd.DataFrame, drop_partial: bool = True) -> pd.DataFrame:
    """ISO-week bars from daily sessions. The current (possibly incomplete)
    week is dropped by default — a weekly bar only exists once its week is
    over, so weekly signals never repaint."""
    iso = daily.index.to_series().apply(lambda d: (d.isocalendar()[0], d.isocalendar()[1]))
    agg = daily.groupby(iso.values).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))
    last_dates = daily.index.to_series().groupby(iso.values).max()
    agg.index = pd.Index(last_dates.values)
    agg = agg.sort_index()
    if drop_partial and len(agg):
        agg = agg.iloc[:-1]
    return agg


def fetch_4h_confirmed(ticker: str, days: int = 200) -> pd.DataFrame:
    """Polygon 4h bars with REAL timestamps (the shared helper collapses them
    to dates), final bar dropped while its 4-hour window is still open — so
    the newest bar is always confirmed."""
    from datetime import date, datetime, timedelta, timezone
    from analysis.polygon_data import get_client
    client = get_client()
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if not client:
        return empty
    try:
        end = date.today()
        start = end - timedelta(days=days + 10)
        aggs = list(client.get_aggs(ticker, 4, "hour",
                                    start.isoformat(), end.isoformat(), limit=50000))
    except Exception as e:
        log.debug(f"[oscillator] 4h fetch {ticker} failed: {e}")
        return empty
    if not aggs:
        return empty
    rows = [(datetime.fromtimestamp(a.timestamp / 1000, tz=timezone.utc),
             a.open, a.high, a.low, a.close, a.volume) for a in aggs]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.set_index("ts").astype(float)
    if len(df) and df.index[-1] + pd.Timedelta(hours=4) > pd.Timestamp.now(tz=timezone.utc):
        df = df.iloc[:-1]
    return df


def _fetch_daily_ohlcv(conn, tickers: list, days: int = 700) -> dict:
    """OHLCV daily bars per ticker (high/low/open fall back to close for any
    row predating the 0062 backfill), oldest → newest."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, trade_date,
                   COALESCE(open, close), COALESCE(high, close),
                   COALESCE(low, close), close, COALESCE(volume, 0)
            FROM daily_prices
            WHERE ticker = ANY(%s) AND trade_date >= CURRENT_DATE - %s
            ORDER BY ticker, trade_date
            """, (tickers, days),
        )
        rows = cur.fetchall()
    out: dict = {}
    for t, dt, o, h, lo, c, v in rows:
        out.setdefault(t, []).append((dt, float(o), float(h), float(lo), float(c), float(v)))
    frames = {}
    for t, recs in out.items():
        df = pd.DataFrame(recs, columns=["ts", "open", "high", "low", "close", "volume"])
        frames[t] = df.set_index("ts")
    return frames


# ── Signal evaluation (last confirmed bar only) ──────────────────────────────

def _pivot_idx(vals: np.ndarray, k: int, kind: str) -> list:
    """Confirmed fractal pivots (need k bars on BOTH sides — no repaint)."""
    out = []
    n = len(vals)
    for i in range(k, n - k):
        w = vals[i - k:i + k + 1]
        if kind == "low" and vals[i] <= w.min():
            out.append(i)
        elif kind == "high" and vals[i] >= w.max():
            out.append(i)
    return out


def evaluate_signals(df: pd.DataFrame, pattern_ctx: dict = None) -> dict:
    """Signals present AT the last confirmed bar. Returns
    {signals: {...}, confluence_score, direction} — deterministic, and by
    construction immutable once the bar has closed."""
    if len(df) < 70:
        return {"signals": {}, "confluence_score": 0, "direction": None}
    sig: dict = {}
    c = df.iloc[-1]
    p = df.iloc[-2]

    # 1) WaveTrend cross, tagged by zone. Crosses inside the ±53 bands are
    # 'weak' — tradeable waves start from the bands.
    if p["wt1"] <= p["wt2"] and c["wt1"] > c["wt2"]:
        zone = "extreme" if c["wt2"] <= -WT_INNER else "weak"
        sig["wt_cross"] = {"dir": "up", "zone": zone, "wt2": round(float(c["wt2"]), 1)}
    elif p["wt1"] >= p["wt2"] and c["wt1"] < c["wt2"]:
        zone = "extreme" if c["wt2"] >= WT_INNER else "weak"
        sig["wt_cross"] = {"dir": "down", "zone": zone, "wt2": round(float(c["wt2"]), 1)}

    # 2) Coil: 10 bars of tight closes while wt1 bleeds ≥15 points, with the
    # band low holding above the nearest confirmed pivot-low shelf below it.
    w10 = df.iloc[-10:]
    band_lo, band_hi = float(w10["close"].min()), float(w10["close"].max())
    if band_lo > 0 and (band_hi / band_lo - 1) <= 0.04 \
            and (df["wt1"].iloc[-10] - c["wt1"]) >= 15:
        lows = df["low"].values[:-10]
        piv = _pivot_idx(lows, 3, "low")
        shelf = max((lows[i] for i in piv[-12:] if lows[i] < band_lo), default=None)
        if shelf is None or band_lo > shelf:
            quality = 50.0
            if pattern_ctx and pattern_ctx.get("direction") == "bullish" \
                    and pattern_ctx.get("status") == "forming":
                quality += 25.0
                inv = pattern_ctx.get("invalid") or 0
                trig = pattern_ctx.get("trigger") or 0
                if inv and trig and inv < band_lo and band_hi < trig:
                    quality += 25.0   # coiling inside the right-shoulder zone
            sig["coil"] = {"band_pct": round((band_hi / band_lo - 1) * 100, 2),
                           "wt1_bleed": round(float(df["wt1"].iloc[-10] - c["wt1"]), 1),
                           "shelf": round(float(shelf), 2) if shelf else None,
                           "coil_quality": quality}

    # 3) %R hook out of the extreme band after ≥3 closes pinned there.
    r = df["pctr"].values
    if r[-1] > -80 and (r[-4:-1] <= -80).all():
        sig["pctr_hook"] = {"dir": "up", "pctr": round(float(r[-1]), 1)}
    elif r[-1] < -20 and (r[-4:-1] >= -20).all():
        sig["pctr_hook"] = {"dir": "down", "pctr": round(float(r[-1]), 1)}

    # 4) MACD histogram flip + signal-line cross.
    if np.sign(c["macd_hist"]) != np.sign(p["macd_hist"]) and p["macd_hist"] != 0:
        sig["macd_flip"] = {"dir": "up" if c["macd_hist"] > 0 else "down",
                            "hist": round(float(c["macd_hist"]), 4)}
    if p["macd"] <= p["macd_signal"] and c["macd"] > c["macd_signal"]:
        sig["macd_cross"] = {"dir": "up"}
    elif p["macd"] >= p["macd_signal"] and c["macd"] < c["macd_signal"]:
        sig["macd_cross"] = {"dir": "down"}

    # 5) Money-flow curl (default variant): slope sign change, flagged
    # volume_backed when the driving bars ran hot — a curl without volume is
    # usually just an old bar rolling out of the window.
    mf = df[MF_DEFAULT].values
    if len(mf) >= 4 and not np.isnan(mf[-4:]).any():
        s_now, s_prev = mf[-1] - mf[-2], mf[-2] - mf[-3]
        if np.sign(s_now) != np.sign(s_prev) and s_prev != 0:
            v20 = df["volume"].rolling(20).mean().iloc[-2]
            hot = bool(v20 and df["volume"].iloc[-2:].mean() > 1.5 * v20)
            sig["mf_curl"] = {"dir": "up" if s_now > 0 else "down",
                              "volume_backed": hot,
                              "mf": round(float(mf[-1]), 2)}

    # 6) Divergence on confirmed swing pivots (price vs wt1), 60-bar window.
    look = df.iloc[-60:]
    ph = _pivot_idx(look["high"].values, 3, "high")
    pl = _pivot_idx(look["low"].values, 3, "low")
    if len(ph) >= 2:
        a, b = ph[-2], ph[-1]
        if look["high"].values[b] > look["high"].values[a] \
                and look["wt1"].values[b] < look["wt1"].values[a] \
                and look["wt1"].values[a] > 0:
            sig["divergence"] = {"dir": "bearish",
                                 "price": [round(float(look["high"].values[a]), 2),
                                           round(float(look["high"].values[b]), 2)],
                                 "wt1": [round(float(look["wt1"].values[a]), 1),
                                         round(float(look["wt1"].values[b]), 1)]}
    if "divergence" not in sig and len(pl) >= 2:
        a, b = pl[-2], pl[-1]
        if look["low"].values[b] < look["low"].values[a] \
                and look["wt1"].values[b] > look["wt1"].values[a] \
                and look["wt1"].values[a] < 0:
            sig["divergence"] = {"dir": "bullish",
                                 "price": [round(float(look["low"].values[a]), 2),
                                           round(float(look["low"].values[b]), 2)],
                                 "wt1": [round(float(look["wt1"].values[a]), 1),
                                         round(float(look["wt1"].values[b]), 1)]}

    score, direction = _confluence(df, sig, pattern_ctx)
    return {"signals": sig, "confluence_score": score, "direction": direction}


def _confluence(df: pd.DataFrame, sig: dict, pattern_ctx: dict = None):
    """0–100. Waves + money flow carry 50, %R 15, MACD 15, structural
    confluence with pattern_scan 20. WaveTrend and MACD are mathematical
    cousins (both EMA-difference machines), so they score in SEPARATE
    buckets and can never double-count the same move. Scored for both
    directions; the stronger side wins."""
    c = df.iloc[-1]
    mf = float(c[MF_DEFAULT]) if not np.isnan(c[MF_DEFAULT]) else 0.0
    mf_slope = float(df[MF_DEFAULT].iloc[-1] - df[MF_DEFAULT].iloc[-2])
    out = {}
    for side in ("bullish", "bearish"):
        up = side == "bullish"
        s = 0.0
        # Waves + money flow (50)
        wt2 = float(c["wt2"])
        if (up and wt2 <= -WT_INNER) or (not up and wt2 >= WT_INNER):
            s += 15 + (5 if abs(wt2) >= WT_OUTER else 0)
        x = sig.get("wt_cross")
        if x and x["dir"] == ("up" if up else "down"):
            s += 12 + (8 if x["zone"] == "extreme" else 0)
        if (up and mf_slope > 0) or (not up and mf_slope < 0):
            s += 5
        if (up and mf < 0) or (not up and mf > 0):
            s += 5   # flow washed out against the move = fuel for the turn
        s = min(s, 50.0)
        # %R (15)
        r = float(c["pctr"])
        if (up and r <= -80) or (not up and r >= -20):
            s += 8
        h = sig.get("pctr_hook")
        if h and h["dir"] == ("up" if up else "down"):
            s += 7
        # MACD (15)
        if (up and c["macd_hist"] > 0) or (not up and c["macd_hist"] < 0):
            s += 5
        f = sig.get("macd_flip")
        if f and f["dir"] == ("up" if up else "down"):
            s += 5
        mx = sig.get("macd_cross")
        if mx and mx["dir"] == ("up" if up else "down"):
            s += 5
        # Structure (20): a live pattern in the same direction, near its zone
        if pattern_ctx and pattern_ctx.get("direction") == side:
            s += 12
            trig = pattern_ctx.get("trigger")
            if trig and abs(float(c["close"]) / trig - 1) <= 0.05:
                s += 8
        out[side] = min(round(s), 100)
    direction = max(out, key=out.get)
    if out["bullish"] == out["bearish"]:
        direction = None
    return (out[direction] if direction else max(out.values())), direction


# ── Scan orchestration ───────────────────────────────────────────────────────

def _pattern_context(conn) -> dict:
    """(ticker, timeframe) -> best live pattern row, for coil boosts and the
    structural-confluence bucket."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (ticker, timeframe)
                   ticker, timeframe, direction, status, trigger_price,
                   invalid_level
            FROM pattern_scan
            ORDER BY ticker, timeframe, score DESC NULLS LAST
        """)
        return {(t, tf): {"direction": d, "status": st,
                          "trigger": float(tr) if tr is not None else None,
                          "invalid": float(inv) if inv is not None else None}
                for t, tf, d, st, tr, inv in cur.fetchall()}


def _store(conn, ticker: str, timeframe: str, df: pd.DataFrame, ev: dict) -> None:
    c = df.iloc[-1]
    bar_ts = df.index[-1]

    def f(v):
        v = float(v)
        return None if (np.isnan(v) or np.isinf(v)) else round(v, 4)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO oscillator_scan
                (ticker, timeframe, bar_ts, wt1, wt2, wt_diff, mf_candle,
                 mf_volume, rsi, stoch_k, stoch_d, pctr, pctr_ema, macd,
                 macd_signal, macd_hist, signals, confluence_score,
                 direction, scanned_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s::jsonb,%s,%s, clock_timestamp())
            ON CONFLICT (ticker, timeframe) DO UPDATE SET
                bar_ts=EXCLUDED.bar_ts, wt1=EXCLUDED.wt1, wt2=EXCLUDED.wt2,
                wt_diff=EXCLUDED.wt_diff, mf_candle=EXCLUDED.mf_candle,
                mf_volume=EXCLUDED.mf_volume, rsi=EXCLUDED.rsi,
                stoch_k=EXCLUDED.stoch_k, stoch_d=EXCLUDED.stoch_d,
                pctr=EXCLUDED.pctr, pctr_ema=EXCLUDED.pctr_ema,
                macd=EXCLUDED.macd, macd_signal=EXCLUDED.macd_signal,
                macd_hist=EXCLUDED.macd_hist, signals=EXCLUDED.signals,
                confluence_score=EXCLUDED.confluence_score,
                direction=EXCLUDED.direction, scanned_at=clock_timestamp()
        """, (ticker, timeframe, str(bar_ts), f(c["wt1"]), f(c["wt2"]),
              f(c["wt_diff"]), f(c["mf_candle"]), f(c["mf_volume"]),
              f(c["rsi"]), f(c["stoch_k"]), f(c["stoch_d"]), f(c["pctr"]),
              f(c["pctr_ema"]), f(c["macd"]), f(c["macd_signal"]),
              f(c["macd_hist"]), json.dumps(ev["signals"], default=str),
              ev["confluence_score"], ev["direction"]))
        for name, detail in ev["signals"].items():
            direction = detail.get("dir") or ev["direction"] or "n/a"
            cur.execute("""
                INSERT INTO oscillator_signals
                    (ticker, timeframe, signal_type, direction, bar_ts,
                     price, context)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (ticker, timeframe, signal_type, bar_ts) DO NOTHING
            """, (ticker, timeframe, name, direction, str(bar_ts),
                  f(c["close"]), json.dumps(detail, default=str)))


def run_oscillator_scan(include_4h: bool = True) -> dict:
    """Full scan over the pattern universe: daily + weekly from the DB, 4h
    from Polygon (same bounded candidate set the pattern scan uses).
    Designed to run immediately after each pattern scan so the structural-
    confluence bucket reads fresh pattern rows."""
    from screen.reversal_screen import _conn
    conn = _conn()
    counts = {"daily": 0, "weekly": 0, "4h": 0}
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
        pctx = _pattern_context(conn)
        log.info(f"[oscillator] daily/weekly scan over {len(tickers)} names")
        t0 = time.time()
        for i in range(0, len(tickers), 120):
            batch = tickers[i:i + 120]
            frames = _fetch_daily_ohlcv(conn, batch)
            for t, daily in frames.items():
                if len(daily) < 70:
                    continue
                try:
                    dfd = compute_oscillator(daily)
                    _store(conn, t, "daily", dfd,
                           evaluate_signals(dfd, pctx.get((t, "daily"))))
                    counts["daily"] += 1
                    wk = resample_weekly(daily)
                    if len(wk) >= 70:
                        dfw = compute_oscillator(wk)
                        _store(conn, t, "weekly", dfw,
                               evaluate_signals(dfw, pctx.get((t, "weekly"))))
                        counts["weekly"] += 1
                except Exception as e:
                    log.debug(f"[oscillator] {t} failed: {e}")
            conn.commit()
        log.info(f"[oscillator] daily {counts['daily']} / weekly "
                 f"{counts['weekly']} in {time.time() - t0:.0f}s")
        if include_4h:
            counts["4h"] = _scan_4h(conn, pctx)
    finally:
        conn.close()
    return counts


def _scan_4h(conn, pctx: dict) -> int:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from analysis.pattern_scan import _four_h_candidates, FOUR_H_WORKERS
    cands = _four_h_candidates(conn, {})
    log.info(f"[oscillator] 4h scan over {len(cands)} candidates")

    def _one(t):
        df = fetch_4h_confirmed(t, days=200)
        if len(df) < 70:
            return None
        dfo = compute_oscillator(df)
        return t, dfo, evaluate_signals(dfo, pctx.get((t, "4h")))

    n = 0
    with ThreadPoolExecutor(max_workers=FOUR_H_WORKERS) as ex:
        futs = {ex.submit(_one, t): t for t in cands}
        for fut in as_completed(futs):
            try:
                res = fut.result()
                if res:
                    _store(conn, res[0], "4h", res[1], res[2])
                    n += 1
            except Exception as e:
                log.warning(f"[oscillator] 4h {futs[fut]} failed: {e}")
    conn.commit()
    log.info(f"[oscillator] 4h: {n} names")
    return n
