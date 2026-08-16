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

TIMEFRAMES = ("daily", "3d", "weekly", "4h", "1h")    # scanned + stored
RESAMPLE_TFS = ("2d", "3d")                     # on-demand, single-ticker reads
ON_DEMAND_TFS = ("daily", "weekly", "monthly", "2d", "3d", "4h", "1h", "5m")

# Polygon aggregate settings per intraday timeframe: (multiplier, timespan,
# calendar-days of history, seconds per bar). 5m is an execution timeframe —
# never fleet-scanned, only computed live for a single ticker on request.
INTRADAY_SPEC = {
    "4h": (4, "hour", 200, 4 * 3600),
    "1h": (1, "hour", 75, 3600),
    "5m": (5, "minute", 10, 300),
}

# Fleet-scan quality gate for the alert-performance feed: a fired signal set
# only logs to alert_log when the bar's confluence clears this.
PERF_MIN_CONFLUENCE = 60

# Bump when signal DEFINITIONS change (new signal types, changed payloads).
# The deploy-time seed sees an unclaimed version and re-runs the full scan,
# so stored signals never lag the code — without this, a new signal shows
# an empty screen until the next scheduled scan.
# v2: stacked divergence (4 indicators + count), mf_round, power coils.
# v3: loaded_spring (the replay-validated "RSI holds 50 while flow/%R dip"
#     cohort) promoted to a signal; coil demoted from screens (no excess
#     edge vs SPY in 61k replayed events — kept computed for continuity).
# v4: ETF universe (etf_theme_map) joins the fleet scan — index/sector
#     structures are where-the-market-leans information.
# v5: cipher_reversal — Eric's NFLX-3D washed-out-and-turning state as a
#     named composite (deep-red flow curving up + fresh wave cross from the
#     lower half + RSI turning). The LNG lesson: mf_round matches the arc
#     SHAPE with no requirement on the LEVEL it turns from, so a healthy
#     uptrend's flow wobble screens identically to a washout unless the
#     deep-red level is a hard leg.
# v6: cipher_reversal grows two legs from same-day calibration (Eric,
#     reviewing the first live list: "these charts do not match"). The
#     v5 legs admitted mid-range ripples (ALG 1h: waves −20/−33, RSI 50
#     in sideways chop) and already-recovered states (STM 1h: RSI 61.6).
#     Location: the wave trough must sit in the lower band. Timing: RSI
#     must still be turning, not recovered — episodes with the wash but
#     RSI > 60 grade −0.194R (n=78) vs +0.146R (n=1,093) at RSI ≤ 60.
# v7: the green-RSI leg (Eric, same evening, AGO vs UNH — "it doesn't
#     have green RSIs like UNH does"). AGO's 4h carried the whole v6
#     stack but its StochRSI pair had already run to 84/64 with the wave
#     cross 7 bars old — the turn was SPENT, and the panel showed no
#     green. UNH: stoch 37/18 curling, cross this bar. So: stoch_d ≤ 50
#     with k ≥ d (the pair still low and curling up), and the wave cross
#     tightens to ≤ 4 bars (NFLX-3D archetype fired at 3; AGO's 7 is a
#     chase). Stoch isn't in the episode record, so this leg is graded
#     live by alert-performance forward returns, stated where surfaced.
# v8: the %R leg (Eric: "add a curving up williams %R from the bottom") —
#     Williams %R(28) pinned at/below −80 inside the last 10 bars and
#     rising on the last confirmed bar. Same live-graded status as the
#     stoch leg: %R isn't in the episode record either.
# v9: still-red-NOW (the COLM case, same evening): flow troughed at −10.4
#     but had recovered to −3.4 by the fire bar — a sliver below zero
#     that renders neutral on the panel, the wash already digested (the
#     AGO disease, in the flow itself). The current flow must still be
#     ≤ −4. Stated honestly: the record is equivocal between −8 and 0
#     (−8..−4 grades +0.054, −4..0 grades +0.085, both under the ≤ −8
#     core's +0.105), so this threshold is LOOK calibration judged by
#     forward returns, not a backtest claim.
# v10: the Williams-%R higher-low family (Eric, 2026-08-15 calibration —
#     CHWY the archetype, NI/MARA the refusals). pctr_hl = the earliest
#     whisper: two confirmed %R(28) floor troughs with REAL lift (a
#     saturated-floor pair like NI's −99.2 → −98.7 is measurement noise,
#     not absorption), tape stabilized, still pre-breakout, %R ≤ −45.
#     base_turn = the SNAP-look confirmation: same %R structure plus
#     everything turning together — MACD hist green with the line still
#     under water, waves crossed up and lifting, RSI mid-band, flow out
#     of deep red, price back above its 8-bar average. At the episodes,
#     %R floor troughs of either kind front-run 1R at 64-66% vs a 57%
#     baseline; expectancy favors the flush (+0.26R) over the higher
#     low (+0.03R) at breakout entries — stated where surfaced; the
#     live claim ("higher lows lead big moves") grades via forward
#     returns.
# v11: shallow pairs are tagged, not skipped (Eric: "I don't want you to
#     skip the small higher low as sometimes those run like they did
#     with CHWY"). A saturated-floor pair now fires with shallow=true
#     and ranks last instead of being refused; the NI/MARA knife-guard
#     is the stabilized-tape leg, which stays hard. The two flavors
#     grade separately via forward returns.
#
# v12-v14 (2026-08-16): the BW-3D archetype experiment — bull_embed (the
#     embedded cruise) then red_to_green (the launch flip), built and
#     calibrated same-evening from the BW 3-day chart. Both RETIRED in
#     v15 the same evening on Eric's chart check of the output ("this
#     absolutely is not it. Remove this from our system"). The '3d'
#     timeframe (busday-epoch buckets, repaint-proof — v13 fixed the
#     date-object index) STAYS: it is neutral scan infrastructure and
#     his archetype charts live on it. Episode grades kept for the
#     record (outliers capped at 10R): embed core UNDERPERFORMED
#     baseline both timeframes; daily just-green flip -0.398R median
#     -1.00 (a trap); weekly flip +0.158R vs +0.117R. The lesson:
#     chart-look composites wait for the labeled exemplar set
#     (2026-08-15 plan) — the eye is not specified by adjectives.
SIGNALS_VERSION = 15

# %R higher-low family legs.
PHL_FLOOR = -70.0          # both troughs at/below this
PHL_LIFT_MIN = 8.0         # near the floor, demand real lift...
PHL_UNSATURATED = -88.0    # ...unless the second trough already left it
PHL_SPACING = (4, 30)      # bars between the two troughs
PHL_FRESH_BARS = 14        # second-trough recency
PHL_UNSPENT_MAX = -45.0    # pctr_hl only: %R not yet spent
PHL_STAB_BARS = 3          # most recent 30-bar closing low at least this old
PHL_PIVOT_K = 2


# cipher_reversal legs: the money-flow trough that counts as "deep in the
# red" (mf_candle scale, typical range ±15), how fresh the wave cross-up
# must be to count as "momentum coming in" rather than history, how deep
# the wave trough must be for the turn to come from the END of a decline
# rather than a mid-range wobble, and the RSI above which a "turn" is
# actually a finished recovery.
CR_MF_DEEP = -8.0
CR_MF_RED_NOW = -4.0
CR_X_FRESH_BARS = 4
CR_WT_TROUGH = -40.0
CR_RSI_MAX = 60.0
CR_STOCH_D_MAX = 50.0
CR_PCTR_FLOOR = -80.0


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
    """ISO-week bars from daily sessions. A partial (still-trading) current
    week is dropped so weekly signals never repaint — but ONLY when it is
    genuinely partial. The old unconditional drop discarded the week that
    closed Friday all weekend long, so Saturday/Sunday weekly reads ran a
    full bar behind the chart (the NU cross-age confusion, META's weekly
    price showing $582). A week is complete once we're past its Friday:
    either today is in a later ISO week, or it's the Sat/Sun of the same
    ISO week."""
    from datetime import date as _date
    iso = daily.index.to_series().apply(lambda d: (d.isocalendar()[0], d.isocalendar()[1]))
    agg = daily.groupby(iso.values).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))
    last_dates = daily.index.to_series().groupby(iso.values).max()
    agg.index = pd.Index(last_dates.values)
    agg = agg.sort_index()
    if drop_partial and len(agg):
        today = _date.today()
        last_bar = pd.Timestamp(agg.index[-1])
        same_week = (today.isocalendar()[:2]
                     == last_bar.date().isocalendar()[:2])
        if same_week and today.weekday() < 5:
            agg = agg.iloc[:-1]
    return agg


def resample_sessions(daily: pd.DataFrame, k: int,
                      drop_partial: bool = True) -> pd.DataFrame:
    """k-session bars bucketed by BUSINESS-day ordinal since a fixed epoch
    (2000-01-03, a Monday) so the bars are STABLE as the fetch window
    slides — an end-anchored grouping re-buckets the entire history every
    session, a repaint machine, which is fatal for STORED rows and
    forward-return grading (and TradingView anchors from series start, so
    stable buckets are also what the chart shows). A holiday inside a
    bucket simply leaves a shorter bar, the same way a holiday shortens a
    week. The current bucket is dropped while genuinely in progress and
    kept once the calendar is past its final business day (weekly's
    partial-bar rule, including its weekend fix)."""
    from datetime import date as _date
    epoch = np.datetime64("2000-01-03")
    # The fleet fetch indexes frames with raw datetime.date objects while
    # tests build DatetimeIndex — .date exists only on the latter, and the
    # scan's per-ticker except swallowed the difference silently (zero 3d
    # rows, weekly skipped behind it, found 2026-08-16 within the hour).
    # Normalize per element so both worlds bucket identically.
    d64 = np.array([np.datetime64(pd.Timestamp(x).date()) for x in daily.index],
                   dtype="datetime64[D]")
    bucket = np.busday_count(epoch, d64) // k
    agg = daily.groupby(bucket).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))
    last_dates = daily.index.to_series().groupby(bucket).max()
    agg.index = pd.Index(last_dates.values)
    agg = agg.sort_index()
    if drop_partial and len(agg):
        last_bar = np.datetime64(pd.Timestamp(agg.index[-1]).date())
        last_bucket = int(np.busday_count(epoch, last_bar) // k)
        bucket_end = np.busday_offset(epoch, last_bucket * k + (k - 1))
        if np.datetime64(_date.today()) <= bucket_end:
            agg = agg.iloc[:-1]
    return agg


def resample_3d(daily: pd.DataFrame, drop_partial: bool = True) -> pd.DataFrame:
    """The scanned '3d' timeframe (2026-08-16, the BW-3D archetype)."""
    return resample_sessions(daily, 3, drop_partial)


def resample_monthly(daily: pd.DataFrame, drop_partial: bool = True) -> pd.DataFrame:
    """Calendar-month bars from daily sessions. The current (incomplete)
    month is dropped by default — a monthly bar only exists once its month
    closes, so monthly signals never repaint. Mid-month monthly reads are
    provisional by definition; this keeps them out of the math."""
    key = daily.index.to_series().apply(lambda d: (d.year, d.month))
    agg = daily.groupby(key.values).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"))
    last_dates = daily.index.to_series().groupby(key.values).max()
    agg.index = pd.Index(last_dates.values)
    agg = agg.sort_index()
    if drop_partial and len(agg):
        # Only drop the last month when it is GENUINELY still in progress —
        # once the calendar has moved on, that month can never repaint.
        from datetime import date as _date
        today = _date.today()
        last_bar = pd.Timestamp(agg.index[-1])
        if (today.year, today.month) == (last_bar.year, last_bar.month):
            agg = agg.iloc[:-1]
    return agg


def fetch_daily_long(ticker: str, days: int = 2600) -> pd.DataFrame:
    """~7 years of daily bars from Polygon — the monthly timeframe needs far
    more history than daily_prices retains (70+ monthly bars for honest
    indicator math). On-demand single-ticker reads only."""
    from datetime import date, datetime, timedelta, timezone
    from analysis.polygon_data import get_client
    client = get_client()
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if not client:
        return empty
    try:
        end = date.today()
        start = end - timedelta(days=days)
        aggs = list(client.get_aggs(ticker, 1, "day",
                                    start.isoformat(), end.isoformat(), limit=50000))
    except Exception as e:
        log.debug(f"[oscillator] long daily fetch {ticker} failed: {e}")
        return empty
    if not aggs:
        return empty
    rows = [(datetime.fromtimestamp(a.timestamp / 1000, tz=timezone.utc).date(),
             a.open, a.high, a.low, a.close, a.volume) for a in aggs]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"])
    return df.set_index("ts").astype(float).sort_index()


def fetch_intraday_confirmed(ticker: str, tf: str = "4h",
                             days: int = None) -> pd.DataFrame:
    """Polygon intraday bars (4h / 1h / 5m) with REAL timestamps (the shared
    helper collapses them to dates), final bar dropped while its window is
    still open — so the newest bar is always confirmed."""
    from datetime import date, datetime, timedelta, timezone
    from analysis.polygon_data import get_client
    mult, span, default_days, bar_secs = INTRADAY_SPEC[tf]
    client = get_client()
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if not client:
        return empty
    try:
        end = date.today()
        start = end - timedelta(days=(days or default_days) + 10)
        aggs = list(client.get_aggs(ticker, mult, span,
                                    start.isoformat(), end.isoformat(), limit=50000))
    except Exception as e:
        log.debug(f"[oscillator] {tf} fetch {ticker} failed: {e}")
        return empty
    if not aggs:
        return empty
    rows = [(datetime.fromtimestamp(a.timestamp / 1000, tz=timezone.utc),
             a.open, a.high, a.low, a.close, a.volume) for a in aggs]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.set_index("ts").astype(float).sort_index()
    if len(df) and df.index[-1] + pd.Timedelta(seconds=bar_secs) \
            > pd.Timestamp.now(tz=timezone.utc):
        df = df.iloc[:-1]
    return df


# A confirmed intraday series whose last bar is older than this is stale —
# the fetch was truncated (CP's 4h series came back ending June 23 while its
# 1h was current). 4 calendar days rides out any weekend + holiday.
STALE_MAX_DAYS = 4


def _is_stale(df: pd.DataFrame) -> bool:
    if not len(df):
        return True
    from datetime import timezone
    age = pd.Timestamp.now(tz=timezone.utc) - df.index[-1]
    return age > pd.Timedelta(days=STALE_MAX_DAYS)


def fetch_intraday_fresh(ticker: str, tf: str) -> pd.DataFrame:
    """fetch_intraday_confirmed + staleness guard: a truncated response gets
    ONE retry with a shorter window (fewer aggregates — dodges whatever cap
    clipped the long request); still-stale series come back empty so callers
    skip the ticker instead of storing weeks-old readings as current."""
    df = fetch_intraday_confirmed(ticker, tf)
    if _is_stale(df):
        df = fetch_intraday_confirmed(ticker, tf, days=45)
        if _is_stale(df):
            log.debug(f"[oscillator] {tf} {ticker}: series stale after retry "
                      f"(last bar {df.index[-1] if len(df) else 'none'}) — skipped")
            return df.iloc[0:0]
    return df


def fetch_4h_confirmed(ticker: str, days: int = 200) -> pd.DataFrame:
    return fetch_intraday_confirmed(ticker, "4h", days)


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


def _pctr_hl_pair(df: pd.DataFrame):
    """Confirmed Williams-%R higher-low pair off the floor with the tape
    stabilized — the structure pctr_hl and base_turn both stand on.
    Pure; returns the pair payload or None.

    Calibrated 2026-08-15 on Eric's charts: CHWY (the archetype) fires;
    an NI-style saturated pair (0.5-point 'higher low' between two bars
    pinned at −99) fires TAGGED shallow and ranks last — small higher
    lows sometimes run, so they're graded, never skipped; MARA is
    refused because its tape was still printing new lows — a wash
    metric maxed by an ONGOING collapse is a knife, not a base."""
    rp = df["pctr"].values[-60:]
    cl = df["close"].values[-60:]
    n = len(rp)
    if n < 40 or np.isnan(rp[-1]):
        return None
    piv = [i for i in _pivot_idx(rp, PHL_PIVOT_K, "low")
           if not np.isnan(rp[i]) and rp[i] <= PHL_FLOOR]
    if len(piv) < 2:
        return None
    i1, i2 = piv[-2], piv[-1]
    lift = float(rp[i2] - rp[i1])
    if rp[i2] <= rp[i1]:
        return None
    # A tiny lift with the second trough still pinned at the saturated
    # floor (the NI look) is TAGGED shallow and ranked last, never
    # skipped — Eric, 2026-08-15: "I don't want you to skip the small
    # higher low as sometimes those run like they did with CHWY." The
    # two flavors grade separately via forward returns; the knife-guard
    # that actually separated NI/MARA from CHWY is the stabilized-tape
    # leg below, and that one stays hard.
    shallow = bool(lift < PHL_LIFT_MIN and rp[i2] <= PHL_UNSATURATED)
    if not (PHL_SPACING[0] <= i2 - i1 <= PHL_SPACING[1]):
        return None
    if not (1 <= n - 1 - i2 <= PHL_FRESH_BARS):
        return None
    if rp[-1] <= rp[i2]:
        return None                       # not lifting off the higher low
    c30 = cl[-30:]
    if cl[-1] > 0.99 * float(np.max(c30)):
        return None                       # already broken out — different trade
    low_age = (len(c30) - 1) - int(np.max(np.where(c30 == np.min(c30))))
    if low_age < PHL_STAB_BARS:
        return None                       # still printing new lows (the MARA trap)
    return {"low1": round(float(rp[i1]), 1), "low2": round(float(rp[i2]), 1),
            "lift": round(lift, 1), "shallow": shallow,
            "low2_bars_ago": int(n - 1 - i2),
            "price_div": bool(cl[i2] <= cl[i1] * 1.01),
            "stable_bars": int(low_age), "pctr": round(float(rp[-1]), 1)}


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
                    and pattern_ctx.get("status") in ("forming", "retest"):
                quality += 25.0
                inv = pattern_ctx.get("invalid") or 0
                trig = pattern_ctx.get("trigger") or 0
                if inv and trig and inv < band_lo and band_hi < trig:
                    quality += 25.0   # coiling inside the right-shoulder zone
            # Power coil (Eric's range-rule read): the wave reset while RSI
            # held the bull side of 50 — sellers drained the oscillator but
            # never won a close. CAH July '26 is the archetype.
            rsi_now = float(c["rsi"]) if not np.isnan(c["rsi"]) else None
            sig["coil"] = {"band_pct": round((band_hi / band_lo - 1) * 100, 2),
                           "wt1_bleed": round(float(df["wt1"].iloc[-10] - c["wt1"]), 1),
                           "shelf": round(float(shelf), 2) if shelf else None,
                           "rsi": round(rsi_now, 1) if rsi_now is not None else None,
                           "power": bool(rsi_now is not None and rsi_now >= 50),
                           "coil_quality": quality + (10.0 if rsi_now is not None
                                                      and rsi_now >= 50 else 0.0)}

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

    # 5b) Money-flow ROUNDING — Eric's "beautiful curvature" read made
    # precise: a smooth U-turn (bullish) or ∩-turn (bearish) in the flow
    # line. Unlike mf_curl (a one-bar inflection), this wants a real arc:
    # the flow declined into a turn, then rose smoothly (>=80% of bars in
    # the turn direction) with a meaningful swing. Thresholds are on the
    # mf_candle scale (typical range ±15).
    mfw = df[MF_DEFAULT].iloc[-20:]
    if len(mfw) == 20 and not mfw.isna().any():
        vals = mfw.values

        def _smooth_turn(v, t_i, up):
            run = v[t_i:]
            if len(run) < 5 or t_i < 3:
                return None                      # need lead-in + follow-through
            d_run = np.diff(run)
            ok = (d_run > 0) if up else (d_run < 0)
            if ok.mean() < 0.8:
                return None                      # not smooth — choppy turn
            pre = np.diff(v[:t_i + 1])
            pre_ok = (pre < 0) if up else (pre > 0)
            if pre_ok.mean() < 0.6:
                return None                      # no arc — it wasn't falling/rising in
            swing = abs(float(run[-1] - run[0]))
            if swing < 3.0:
                return None
            return {"dir": "up" if up else "down",
                    "turn_bars_ago": int(len(v) - 1 - t_i),
                    "swing": round(swing, 2),
                    "mf": round(float(v[-1]), 2)}

        r_round = _smooth_turn(vals, int(np.argmin(vals)), True) \
            or _smooth_turn(vals, int(np.argmax(vals)), False)
        if r_round:
            sig["mf_round"] = r_round

    # 6) STACKED divergence on confirmed swing pivots, 60-bar window: the
    # same two price pivots are checked against FOUR series — the wave
    # (wt1), RSI, money flow, and MACD histogram. Each diverges for a
    # different reason (momentum exhaustion / weaker closes / distribution
    # / thrust decay), so agreement is information: count says how many.
    # The first pivot must sit on the elevated (bearish) / depressed
    # (bullish) side of each series' midline — a divergence from nowhere
    # isn't one.
    look = df.iloc[-60:]
    DIV_SERIES = (("wave", "wt1", 0.0), ("rsi", "rsi", 50.0),
                  ("mf", MF_DEFAULT, 0.0), ("macd", "macd_hist", 0.0))

    def _div_hits(a, b, bearish):
        hits = []
        for nm, col, mid in DIV_SERIES:
            va, vb = look[col].values[a], look[col].values[b]
            if np.isnan(va) or np.isnan(vb):
                continue
            if bearish and va > mid and vb < va:
                hits.append(nm)
            elif not bearish and va < mid and vb > va:
                hits.append(nm)
        return hits

    ph = _pivot_idx(look["high"].values, 3, "high")
    pl = _pivot_idx(look["low"].values, 3, "low")
    if len(ph) >= 2:
        a, b = ph[-2], ph[-1]
        if look["high"].values[b] > look["high"].values[a]:
            hits = _div_hits(a, b, bearish=True)
            if hits:
                sig["divergence"] = {"dir": "bearish", "count": len(hits),
                                     "indicators": hits,
                                     "price": [round(float(look["high"].values[a]), 2),
                                               round(float(look["high"].values[b]), 2)]}
    if "divergence" not in sig and len(pl) >= 2:
        a, b = pl[-2], pl[-1]
        if look["low"].values[b] < look["low"].values[a]:
            hits = _div_hits(a, b, bearish=False)
            if hits:
                sig["divergence"] = {"dir": "bullish", "count": len(hits),
                                     "indicators": hits,
                                     "price": [round(float(look["low"].values[a]), 2),
                                               round(float(look["low"].values[b]), 2)]}

    # 7) LOADED SPRING — Eric's "locked and loaded" read, validated by the
    # replay: an oscillator dip in a name whose RSI never surrenders 50.
    # Two proven flavors (excess vs SPY over 21 days): money flow arcing
    # DOWN while RSI holds (+4.4%, n=13.8k) and a %R hook up off a pinned
    # band while RSI holds (+2.7%, n=5.7k). Both are shallow digestion in
    # strength, not distribution — bullish only, by construction. This is
    # what the coil was trying to be; the coil itself showed NO excess edge
    # (61k events, both flavors negative) and is demoted from the screens.
    rsi_last = float(c["rsi"]) if not np.isnan(c["rsi"]) else None
    if rsi_last is not None and rsi_last >= 50:
        flavors = []
        mr = sig.get("mf_round")
        if mr and mr["dir"] == "down":
            flavors.append("mf_down")
        hk = sig.get("pctr_hook")
        if hk and hk["dir"] == "up":
            flavors.append("pctr_hook")
        if flavors:
            sig["loaded_spring"] = {"rsi": round(rsi_last, 1),
                                    "flavors": flavors}

    # 8) CIPHER REVERSAL — Eric's NFLX-3D state (2026-08-14) as a named
    # composite, bullish only. Four hard legs: (a) money flow washed out
    # DEEP (trough <= CR_MF_DEEP inside the last 10 bars) and still red;
    # (b) curving up — three strictly rising closes of the flow line, or a
    # confirmed mf_round up; (c) waves crossing up from the lower half,
    # cross no older than CR_X_FRESH_BARS; (d) RSI turning up. The MACD
    # higher-low is Eric's "nice if they align" — recorded as full_stack,
    # never required. Exists as a composite because no single component
    # can screen for the state: LNG's 4h (MF +1.9, %R −3) fired mf_round
    # on the arc shape alone and read as a match when it was a healthy
    # uptrend's wobble.
    mfv = df[MF_DEFAULT].values
    if len(mfv) >= 12 and not np.isnan(mfv[-12:]).any() \
            and rsi_last is not None and not np.isnan(p["rsi"]):
        trough = float(np.min(mfv[-10:]))
        # v9 (the COLM/GOOS case): "deep in the red and curving up" means
        # the flow is red NOW, not that it visited the red last week. A
        # trough that recovered to a sliver below zero (COLM −10.4 → −3.4,
        # GOOS −10.0 → −1.2) renders neutral on the panel — the wash is
        # already digested.
        deep = trough <= CR_MF_DEEP and mfv[-1] <= CR_MF_RED_NOW
        mr = sig.get("mf_round") or {}
        curving = bool(mfv[-1] > mfv[-2] > mfv[-3]) or mr.get("dir") == "up"
        bsc = _bars_since_cross(df)
        # Location leg (v6): the turn must come out of the LOWER BAND — a
        # cross whose wave trough never left mid-range is a wobble, not a
        # reversal, no matter how red the flow got.
        wt2_trough = float(np.nanmin(df["wt2"].values[-10:]))
        wave_turn = bool(c["wt1"] > c["wt2"]) and float(c["wt2"]) <= 0 \
            and wt2_trough <= CR_WT_TROUGH \
            and bsc is not None and bsc <= CR_X_FRESH_BARS
        # Timing leg (v6): turning, not recovered — RSI above the ceiling
        # means the reversal already happened and this bar is chasing it.
        rsi_turn = rsi_last > float(p["rsi"]) and rsi_last <= CR_RSI_MAX
        # Green-RSI leg (v7): the StochRSI pair must still be low and
        # curling up. A stoch that already ran (AGO: 84/64, cross 7 bars
        # old) is the same washed chart with the turn already spent.
        sk = float(c["stoch_k"]) if not np.isnan(c["stoch_k"]) else None
        sd = float(c["stoch_d"]) if not np.isnan(c["stoch_d"]) else None
        green_rsi = sk is not None and sd is not None \
            and sd <= CR_STOCH_D_MAX and sk >= sd
        # %R leg (v8): Williams %R(28) curving up off the floor — pinned
        # at/below −80 inside the last 10 bars, rising this bar.
        rv = df["pctr"].values
        pctr_min = float(np.nanmin(rv[-10:]))
        pctr_curl = (not np.isnan(rv[-1]) and not np.isnan(rv[-2])
                     and pctr_min <= CR_PCTR_FLOOR and rv[-1] > rv[-2])
        if deep and curving and wave_turn and rsi_turn and green_rsi \
                and pctr_curl:
            mh = df["macd_hist"].iloc[-60:].values
            piv = _pivot_idx(mh, 3, "low")
            troughs = [float(mh[i]) for i in piv
                       if not np.isnan(mh[i]) and mh[i] < 0]
            macd_hl = bool(len(troughs) >= 2 and troughs[-1] > troughs[-2]) \
                or (sig.get("macd_cross") or {}).get("dir") == "up"
            dv = sig.get("divergence") or {}
            sig["cipher_reversal"] = {
                "dir": "up",
                # Eric wants the flow turn "rounded and not jagged" — a
                # confirmed mf_round arc is the archetype and ranks first;
                # a jagged three-bar rise qualifies but says so.
                "rounded": mr.get("dir") == "up",
                "mf": round(float(mfv[-1]), 2),
                "mf_trough": round(trough, 2),
                "wt2": round(float(c["wt2"]), 1),
                "wt_trough": round(wt2_trough, 1),
                "x_up_bars_ago": int(bsc),
                "rsi": round(rsi_last, 1),
                "stoch_k": round(sk, 1),
                "stoch_d": round(sd, 1),
                "pctr": round(float(rv[-1]), 1),
                "pctr_min": round(pctr_min, 1),
                "macd_hl": macd_hl,
                "div_bull": int(dv.get("count") or 0) if dv.get("dir") == "bullish" else 0,
                "full_stack": bool(macd_hl),
            }

    # 9/10) The Williams-%R higher-low family (Eric, 2026-08-15) — one
    # reversal, instrumented at two more ages beside cipher_reversal:
    # pctr_hl is the earliest whisper (structure present, %R still washed);
    # base_turn is the SNAP-look confirmation (same structure, everything
    # turning together). Both bullish only; both structurally unable to
    # touch the confluence score, same as cipher_reversal.
    pair = _pctr_hl_pair(df)
    if pair is not None:
        if pair["pctr"] <= PHL_UNSPENT_MAX:
            sig["pctr_hl"] = dict(pair, dir="up")
        mh_last = float(c["macd_hist"]) if not np.isnan(c["macd_hist"]) else None
        macd_last = float(c["macd"]) if not np.isnan(c["macd"]) else None
        mf_last = float(df[MF_DEFAULT].values[-1]) \
            if not np.isnan(df[MF_DEFAULT].values[-1]) else None
        sma8 = float(np.mean(df["close"].values[-8:]))
        if (rsi_last is not None and mh_last is not None
                and macd_last is not None and mf_last is not None
                and mh_last > 0 and macd_last <= 0        # green hist, line under water
                and c["wt1"] > c["wt2"]
                and -50 <= float(c["wt2"]) <= 15          # waves lifting, not extended
                and 40 <= rsi_last <= 60                  # mid-band, room to run
                and mf_last >= -10                        # flow out of the deep red
                and float(c["close"]) >= sma8):           # price reclaimed the average
            sig["base_turn"] = dict(pair, dir="up",
                                    wt2=round(float(c["wt2"]), 1),
                                    macd=round(macd_last, 2),
                                    macd_hist=round(mh_last, 3),
                                    rsi=round(rsi_last, 1))

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


# ── On-demand single-ticker read (any timeframe, always current) ────────────

def compute_for_ticker(ticker: str, timeframe: str = "daily",
                       conn=None) -> dict:
    """Fresh oscillator read for ONE ticker on any supported timeframe.
    daily/weekly/2d/3d come from daily_prices; 4h/1h/5m from Polygon.
    Returns {} when there's not enough history. Always computed live —
    single-ticker reads are cheap, so nothing here can be stale."""
    ticker = ticker.upper().strip()
    if timeframe not in ON_DEMAND_TFS:
        return {}
    own_conn = conn is None
    if timeframe == "monthly":
        # Needs ~7 years of history — Polygon on demand; fall back to the DB
        # (which will usually be too short and return {} honestly).
        long_daily = fetch_daily_long(ticker)
        if len(long_daily) < 300:
            if own_conn:
                from screen.reversal_screen import _conn
                conn = _conn()
            try:
                long_daily = _fetch_daily_ohlcv(conn, [ticker], days=2600) \
                    .get(ticker, long_daily)
            finally:
                if own_conn:
                    conn.close()
                    conn = None
        df = resample_monthly(long_daily) if len(long_daily) else long_daily
        pctx_tf = None
    elif timeframe in INTRADAY_SPEC:
        df = fetch_intraday_fresh(ticker, timeframe)
        pctx_tf = timeframe if timeframe == "4h" else None
    else:
        if own_conn:
            from screen.reversal_screen import _conn
            conn = _conn()
        try:
            frames = _fetch_daily_ohlcv(conn, [ticker])
        finally:
            if own_conn:
                conn.close()
                conn = None
        daily = frames.get(ticker)
        if daily is None:
            return {}
        if timeframe == "daily":
            df = daily
        elif timeframe == "weekly":
            df = resample_weekly(daily)
        else:
            # Epoch-anchored so the chat read matches the STORED 3d screen
            # bar-for-bar (resample_days is end-anchored and repaints).
            df = resample_sessions(daily, int(timeframe[0]))
        pctx_tf = timeframe if timeframe in ("daily", "weekly") else None
    if len(df) < 70:
        return {}
    dfo = compute_oscillator(df)
    pctx = None
    if pctx_tf:
        try:
            if own_conn:
                from screen.reversal_screen import _conn
                conn = _conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT direction, status, trigger_price, invalid_level
                    FROM pattern_scan WHERE ticker = %s AND timeframe = %s
                    ORDER BY score DESC NULLS LAST LIMIT 1
                """, (ticker, pctx_tf))
                r = cur.fetchone()
            if r:
                pctx = {"direction": r[0], "status": r[1],
                        "trigger": float(r[2]) if r[2] is not None else None,
                        "invalid": float(r[3]) if r[3] is not None else None}
        except Exception:
            pass
        finally:
            if own_conn and conn is not None:
                conn.close()
    ev = evaluate_signals(dfo, pctx)
    c = dfo.iloc[-1]

    def f(v):
        v = float(v)
        return None if (np.isnan(v) or np.isinf(v)) else round(v, 4)
    return {
        "ticker": ticker, "timeframe": timeframe,
        "bar_ts": dfo.index[-1], "close": f(c["close"]),
        "wt1": f(c["wt1"]), "wt2": f(c["wt2"]), "wt_diff": f(c["wt_diff"]),
        "mf": f(c[MF_DEFAULT]), "mf_candle": f(c["mf_candle"]),
        "mf_volume": f(c["mf_volume"]), "rsi": f(c["rsi"]),
        "stoch_k": f(c["stoch_k"]), "stoch_d": f(c["stoch_d"]),
        "pctr": f(c["pctr"]), "pctr_ema": f(c["pctr_ema"]),
        "macd": f(c["macd"]), "macd_signal": f(c["macd_signal"]),
        "macd_hist": f(c["macd_hist"]),
        "bars_since_cross": _bars_since_cross(dfo),
        "signals": ev["signals"], "confluence_score": ev["confluence_score"],
        "direction": ev["direction"], "pattern_ctx": pctx,
    }


# ── Scan orchestration ───────────────────────────────────────────────────────

def _bars_since_cross(df: pd.DataFrame, window: int = 120) -> int:
    """Confirmed bars since wt1 last crossed wt2 (either direction). 0 =
    crossed on the latest bar. None when there's no cross inside the
    window. This is STATE, not an event — the NU lesson: a summary that
    only reports cross EVENTS reads 'not crossed' four weeks after a cross
    that held, which inverts the actual read."""
    try:
        d = (df["wt1"] - df["wt2"]).values[-window:]
        d = d[~np.isnan(d)]
        if len(d) < 2:
            return None
        last = np.sign(d[-1])
        if last == 0:
            return 0
        n = 0
        for v in d[-2::-1]:
            if np.sign(v) != last:
                return n
            n += 1
        return None
    except Exception:
        return None


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
                 macd_signal, macd_hist, bars_since_cross, signals,
                 confluence_score, direction, scanned_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s::jsonb,%s,%s, clock_timestamp())
            ON CONFLICT (ticker, timeframe) DO UPDATE SET
                bar_ts=EXCLUDED.bar_ts, wt1=EXCLUDED.wt1, wt2=EXCLUDED.wt2,
                wt_diff=EXCLUDED.wt_diff, mf_candle=EXCLUDED.mf_candle,
                mf_volume=EXCLUDED.mf_volume, rsi=EXCLUDED.rsi,
                stoch_k=EXCLUDED.stoch_k, stoch_d=EXCLUDED.stoch_d,
                pctr=EXCLUDED.pctr, pctr_ema=EXCLUDED.pctr_ema,
                macd=EXCLUDED.macd, macd_signal=EXCLUDED.macd_signal,
                macd_hist=EXCLUDED.macd_hist,
                bars_since_cross=EXCLUDED.bars_since_cross,
                signals=EXCLUDED.signals,
                confluence_score=EXCLUDED.confluence_score,
                direction=EXCLUDED.direction, scanned_at=clock_timestamp()
        """, (ticker, timeframe, str(bar_ts), f(c["wt1"]), f(c["wt2"]),
              f(c["wt_diff"]), f(c["mf_candle"]), f(c["mf_volume"]),
              f(c["rsi"]), f(c["stoch_k"]), f(c["stoch_d"]), f(c["pctr"]),
              f(c["pctr_ema"]), f(c["macd"]), f(c["macd_signal"]),
              f(c["macd_hist"]), _bars_since_cross(df),
              json.dumps(ev["signals"], default=str),
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


def _perf_entry(ticker: str, timeframe: str, df: pd.DataFrame, ev: dict):
    """alert_log row for the performance pipeline — only for bars where the
    fired signals + confluence clear the quality gate. wt_cross must be from
    the extreme zone; the timing signals (loaded spring, divergence, hook,
    backed curl) qualify on their own. The coil no longer qualifies — the
    replay showed no excess edge, so it doesn't earn perf tracking."""
    sig = ev["signals"]
    if not sig:
        return None
    if ev["direction"] is None \
            or ev["confluence_score"] < PERF_MIN_CONFLUENCE:
        return None
    x = sig.get("wt_cross")
    curl = sig.get("mf_curl") or {}
    qualifying = [k for k in ("loaded_spring", "divergence", "pctr_hook",
                              "cipher_reversal", "pctr_hl", "base_turn") if k in sig]
    if x and x["zone"] == "extreme":
        qualifying.append("wt_cross")
    if curl.get("volume_backed"):
        qualifying.append("mf_curl")
    if not qualifying:
        return None
    direction = ev["direction"]
    return {
        "ticker": ticker,
        "price": float(df["close"].iloc[-1]),
        "score": ev["confluence_score"],
        "signal_type": f"{timeframe}:{'+'.join(sorted(qualifying))}"
                       f":{direction}",
        "timeframe": timeframe,
        "direction": direction,
        "confluence": ev["confluence_score"],
    }


def _log_perf(perf_rows: list) -> None:
    """Feed qualifying fired signals into alert_log so the existing
    performance pipeline computes their 7/30/90-day forward returns."""
    if not perf_rows:
        return
    try:
        from analysis.alert_tracker import log_alerts
        n = log_alerts(perf_rows, "oscillator")
        log.info(f"[oscillator] {n} signals logged to alert-performance")
    except Exception as e:
        log.warning(f"[oscillator] perf logging failed (non-fatal): {e}")


def run_oscillator_scan(include_4h: bool = True,
                        include_daily_weekly: bool = True) -> dict:
    """Full scan over the pattern universe: daily + weekly from the DB, then
    4h AND 1h from Polygon (same bounded candidate set the pattern scan
    uses). Designed to run immediately after each pattern scan so the
    structural-confluence bucket reads fresh pattern rows. The midday
    refresh passes include_daily_weekly=False — daily/weekly bars can't
    change intraday, only the 4h/1h reads can."""
    from screen.reversal_screen import _conn
    conn = _conn()
    counts = {"daily": 0, "3d": 0, "weekly": 0, "4h": 0, "1h": 0}
    perf_rows: list = []
    try:
        try:
            with conn.cursor() as _c:
                _c.execute("SET statement_timeout = '600s'")
            conn.commit()
        except Exception:
            pass
        pctx = _pattern_context(conn)
        if include_daily_weekly:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ticker FROM screener_snapshot
                    UNION SELECT ticker FROM watchlist WHERE active = true
                    UNION SELECT ticker FROM etf_theme_map
                """)
                tickers = sorted({r[0] for r in cur.fetchall() if r[0]})
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
                        ev = evaluate_signals(dfd, pctx.get((t, "daily")))
                        _store(conn, t, "daily", dfd, ev)
                        counts["daily"] += 1
                        pe = _perf_entry(t, "daily", dfd, ev)
                        if pe:
                            perf_rows.append(pe)
                        d3 = resample_3d(daily)
                        if len(d3) >= 70:
                            df3 = compute_oscillator(d3)
                            # no pattern_scan rows exist for 3d — no ctx
                            ev3 = evaluate_signals(df3, None)
                            _store(conn, t, "3d", df3, ev3)
                            counts["3d"] += 1
                            p3 = _perf_entry(t, "3d", df3, ev3)
                            if p3:
                                perf_rows.append(p3)
                        wk = resample_weekly(daily)
                        if len(wk) >= 70:
                            dfw = compute_oscillator(wk)
                            evw = evaluate_signals(dfw, pctx.get((t, "weekly")))
                            _store(conn, t, "weekly", dfw, evw)
                            counts["weekly"] += 1
                            pw = _perf_entry(t, "weekly", dfw, evw)
                            if pw:
                                perf_rows.append(pw)
                    except Exception as e:
                        log.debug(f"[oscillator] {t} failed: {e}")
                conn.commit()
            log.info(f"[oscillator] daily {counts['daily']} / 3d {counts['3d']} "
                     f"/ weekly {counts['weekly']} in {time.time() - t0:.0f}s")
        if include_4h:
            for tf in ("4h", "1h"):
                counts[tf] = _scan_intraday(conn, pctx, tf, perf_rows)
    finally:
        conn.close()
    _log_perf(perf_rows)
    return counts


def _scan_intraday(conn, pctx: dict, tf: str, perf_rows: list) -> int:
    """4h/1h pass over the pattern scan's bounded candidate set."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from analysis.pattern_scan import _four_h_candidates, FOUR_H_WORKERS
    cands = _four_h_candidates(conn, {})
    log.info(f"[oscillator] {tf} scan over {len(cands)} candidates")

    def _one(t):
        df = fetch_intraday_fresh(t, tf)
        if len(df) < 70:
            return None
        dfo = compute_oscillator(df)
        # pattern rows only exist for 4h among intraday TFs
        return t, dfo, evaluate_signals(
            dfo, pctx.get((t, "4h")) if tf == "4h" else None)

    n = 0
    with ThreadPoolExecutor(max_workers=FOUR_H_WORKERS) as ex:
        futs = {ex.submit(_one, t): t for t in cands}
        for fut in as_completed(futs):
            try:
                res = fut.result()
                if res:
                    _store(conn, res[0], tf, res[1], res[2])
                    pe = _perf_entry(res[0], tf, res[1], res[2])
                    if pe:
                        perf_rows.append(pe)
                    n += 1
            except Exception as e:
                log.warning(f"[oscillator] {tf} {futs[fut]} failed: {e}")
    conn.commit()
    log.info(f"[oscillator] {tf}: {n} names")
    return n


# ── Plain-English readout ────────────────────────────────────────────────────

def _fmt_bar_ts(bar_ts, timeframe: str) -> str:
    """Bar stamp for humans — Eastern, 12-hour for intraday bars, plain date
    for daily and up."""
    try:
        ts = pd.Timestamp(bar_ts)
        if timeframe in INTRADAY_SPEC:
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            ts = ts.tz_convert("America/New_York")
            return ts.strftime("%b %d, %I:%M %p ET").replace(" 0", " ")
        return ts.strftime("%b %d")
    except Exception:
        return str(bar_ts)


def describe_read(r: dict) -> str:
    """One compact human line for a compute_for_ticker / oscillator_scan
    read: direction + confluence, wave position, fuel, %R, MACD, and any
    fired signals."""
    if not r:
        return "not enough history"
    wt1, wt2 = r.get("wt1"), r.get("wt2")
    parts = []
    if wt2 is not None:
        if wt2 <= -WT_OUTER:
            pos = f"blown out low ({wt2:+.0f}, below −{WT_OUTER:.0f})"
        elif wt2 <= -WT_INNER:
            pos = f"in the lower band ({wt2:+.0f})"
        elif wt2 >= WT_OUTER:
            pos = f"blown out high ({wt2:+.0f}, above +{WT_OUTER:.0f})"
        elif wt2 >= WT_INNER:
            pos = f"in the upper band ({wt2:+.0f})"
        else:
            pos = f"mid-range ({wt2:+.0f})"
        # State the STANDING wave relationship, not just the anchor wave —
        # a month-old cross that held is a material fact (the NU lesson:
        # "wave −44, rising" read as 'not crossed' when wt1 had been above
        # wt2 for weeks).
        if wt1 is not None:
            state = "crossed UP" if wt1 > wt2 else "crossed DOWN"
            since = r.get("bars_since_cross")
            ago = (f" {since} bars ago" if since not in (None, 0)
                   else (" this bar" if since == 0 else ""))
            parts.append(f"waves {state}{ago} (wt1 {wt1:+.0f} / wt2 {wt2:+.0f}), {pos}")
        else:
            wd = r.get("wt_diff")
            trend = (", wt1 above" if wd and wd > 0
                     else (", wt1 below" if wd else ""))
            parts.append(f"wave {pos}{trend}")
    mf = r.get("mf")
    if mf is not None:
        if mf <= -5:
            parts.append(f"flow washed out ({mf:+.1f})")
        elif mf >= 5:
            parts.append(f"flow loaded ({mf:+.1f})")
        else:
            parts.append(f"flow neutral ({mf:+.1f})")
    pr = r.get("pctr")
    if pr is not None:
        if pr <= -80:
            parts.append(f"%R pinned oversold ({pr:.0f})")
        elif pr >= -20:
            parts.append(f"%R pinned overbought ({pr:.0f})")
        else:
            parts.append(f"%R {pr:.0f}")
    mh = r.get("macd_hist")
    if mh is not None:
        parts.append(f"MACD {'confirming up' if mh > 0 else 'confirming down'}"
                     f" ({mh:+.2f})")
    sig = r.get("signals") or {}
    if sig:
        names = []
        for k, v in sig.items():
            d = v.get("dir") if isinstance(v, dict) else None
            zone = v.get("zone") if isinstance(v, dict) else None
            tag = k + (f" {d}" if d else "")
            if zone == "extreme":
                tag += " (extreme)"
            if isinstance(v, dict) and v.get("volume_backed"):
                tag += " (vol-backed)"
            if isinstance(v, dict) and v.get("count") and v.get("indicators"):
                tag += f" ({v['count']}/4: {'+'.join(v['indicators'])})"
            if isinstance(v, dict) and v.get("flavors"):
                tag += f" ({'+'.join(v['flavors'])}, RSI {v.get('rsi')})"
            names.append(tag)
        parts.append("fired: " + ", ".join(names))
    head = ""
    if r.get("direction"):
        head = f"{r['direction']} {r.get('confluence_score', 0)}/100 — "
    return head + "; ".join(parts)
