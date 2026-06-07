"""
Watchtower — Hidden Gems / Up-and-Comer screen.

Completely separate from momentum_screen.py. This is NOT "strong getting stronger" —
it hunts for stocks that most investors have NOT yet put on their radar.

Data strategy (two-phase to handle ~10k tickers efficiently):
  Phase 1 — Polygon snapshot pass: pull all US equity snapshots in one API call,
             apply cheap filters (price, volume, change%) to cut to ~500 candidates.
  Phase 2 — Historical bars: fetch 300d of daily bars from Polygon for each
             candidate, score base breakout, RSI lift, volume accumulation.
  Phase 3 — Supabase fundamentals: enrich survivors with revenue/earnings/analyst
             data from financial_scores and analyst_revisions.

Scoring model (sum to 100):
  - Base breakout (long consolidation + volume expansion):   25 pts
  - RSI lift from low / momentum starting:                  20 pts
  - Fundamental acceleration (rev/earnings trend):          20 pts
  - Small/mid cap bonus (dollar volume proxy):              10 pts
  - Sector heat / tailwind emerging:                        10 pts
  - Analyst catalyst (big PT upside or uncovered):          10 pts
  - Volume accumulation (up-vol / down-vol):                 5 pts

Usage:
    set -a && source .env && set +a
    python3 screen/upcomer_screen.py
    python3 screen/upcomer_screen.py --ticker ACMR
    python3 screen/upcomer_screen.py --min-score 40
"""
import argparse
import os
import sys
import time
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
    import pandas as pd
except ImportError as e:
    print(f"ERROR: missing dependency: {e}. Run: pip install pandas numpy", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reversal_screen import (
    _conn,
    compute_rsi,
    compute_ema,
    compute_macd,
    compute_volume_ratio,
    EMA_SHORT, EMA_LONG, EMA_TREND, RSI_PERIOD,
)

# ── Config ───────────────────────────────────────────────────────────────────
MIN_DRAWDOWN = 0.18    # at least 18% off 52w high — not a momentum name
MAX_DRAWDOWN = 0.60    # not a completely destroyed stock
MIN_PRICE    = 2.0     # filter out penny stocks
MAX_PRICE    = 500.0   # keep small/mid focused
MIN_AVG_VOL  = 50_000  # minimum average daily volume (liquidity floor)

# Scoring weights
W_BASE_BREAKOUT  = 0.25
W_RSI_LIFT       = 0.20
W_FUNDAMENTAL    = 0.20
W_SMALL_CAP      = 0.10
W_SECTOR         = 0.10
W_ANALYST        = 0.10
W_VOLUME_ACCUM   = 0.05


# ── Polygon helpers ──────────────────────────────────────────────────────────

def _get_polygon_client():
    try:
        from analysis.polygon_data import get_client
        return get_client()
    except Exception:
        return None


def _fetch_all_snapshots() -> List[dict]:
    """
    Pull Polygon snapshot for ALL US stocks in one API call.
    Returns list of dicts with ticker, price, change_pct, volume, avg_volume.
    Filters to basic liquidity/price thresholds on the way out.
    """
    client = _get_polygon_client()
    if not client:
        return []

    candidates = []
    try:
        snapshots = client.get_snapshot_all("stocks", include_otc=False)
        for s in snapshots:
            try:
                ticker = getattr(s, "ticker", None)
                if not ticker or len(ticker) > 5:  # skip options/warrants
                    continue

                day = getattr(s, "day", None)
                prev_day = getattr(s, "prev_day", None)

                if not day:
                    continue

                price = getattr(day, "c", None) or getattr(day, "close", None)
                volume = getattr(day, "v", None) or getattr(day, "volume", None)
                prev_close = getattr(prev_day, "c", None) if prev_day else None

                if not price or not volume:
                    continue

                # Cheap pre-filters
                if price < MIN_PRICE or price > MAX_PRICE:
                    continue
                if volume < MIN_AVG_VOL:
                    continue

                change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

                # Get 52w high from Polygon snapshot if available
                min_52w = getattr(s, "min", None)
                max_52w = getattr(s, "max", None)
                hi_52w = getattr(max_52w, "price", None) if max_52w else None
                lo_52w = getattr(min_52w, "price", None) if min_52w else None

                candidates.append({
                    "ticker": ticker,
                    "price": price,
                    "volume": volume,
                    "change_pct": change_pct,
                    "hi_52w": hi_52w,
                    "lo_52w": lo_52w,
                    "prev_close": prev_close,
                })
            except Exception:
                continue
    except Exception as e:
        print(f"[upcomer] Snapshot fetch error: {e}", file=sys.stderr)

    return candidates


def _phase1_filter(snapshots: List[dict]) -> List[dict]:
    """
    Apply cheap drawdown + basic filters using snapshot data.
    Cuts ~10k tickers down to ~300-600 candidates for Phase 2.
    """
    out = []
    for s in snapshots:
        price = s.get("price", 0)
        hi_52w = s.get("hi_52w")

        if hi_52w and hi_52w > 0 and price > 0:
            drawdown = (hi_52w - price) / hi_52w
            if MIN_DRAWDOWN <= drawdown <= MAX_DRAWDOWN:
                s["drawdown_est"] = drawdown
                out.append(s)
        else:
            # No 52w high in snapshot — include with unknown drawdown, let Phase 2 decide
            s["drawdown_est"] = None
            out.append(s)

    return out


def _fetch_bars_polygon(ticker: str, days: int = 300) -> List[dict]:
    """Fetch daily bars from Polygon for a single ticker."""
    client = _get_polygon_client()
    if not client:
        return []
    try:
        end = date.today()
        start = end - timedelta(days=days + 60)
        aggs = list(client.get_aggs(
            ticker,
            multiplier=1,
            timespan="day",
            from_=start.isoformat(),
            to=end.isoformat(),
            limit=50000,
        ))
        bars = []
        for a in aggs:
            bars.append({
                "trade_date": date.fromtimestamp(a.timestamp / 1000),
                "close": a.close,
                "volume": a.volume,
                "high": a.high,
                "low": a.low,
                "open": a.open,
            })
        return bars
    except Exception:
        return []


def _bars_to_df(bars: List[dict]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars).sort_values("trade_date").reset_index(drop=True)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df


# ── Supabase fundamental enrichment ─────────────────────────────────────────

def _load_signal_data(conn, tickers: List[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {t: {} for t in tickers}
    if not tickers:
        return out
    placeholders = ",".join(["%s"] * len(tickers))

    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT ON (ticker) ticker, grade_consensus,
                       price_target_avg, price_target_high
                FROM analyst_revisions
                WHERE ticker IN ({placeholders})
                ORDER BY ticker, date DESC
                """,
                tickers,
            )
            for row in cur.fetchall():
                t, grade, pt_avg, pt_high = row
                out[t]["grade_consensus"] = grade
                out[t]["price_target_avg"] = float(pt_avg) if pt_avg else None
                out[t]["price_target_high"] = float(pt_high) if pt_high else None
    except Exception:
        pass

    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT ON (ticker) ticker, piotroski_score,
                       revenue_growth_qoq, revenue_growth_yoy, earnings_growth_qoq
                FROM financial_scores
                WHERE ticker IN ({placeholders})
                ORDER BY ticker, as_of_date DESC
                """,
                tickers,
            )
            for row in cur.fetchall():
                t, pio, rev_qoq, rev_yoy, earn_qoq = row
                out[t]["piotroski_score"] = pio
                out[t]["revenue_growth_qoq"] = float(rev_qoq) if rev_qoq else None
                out[t]["revenue_growth_yoy"] = float(rev_yoy) if rev_yoy else None
                out[t]["earnings_growth_qoq"] = float(earn_qoq) if earn_qoq else None
    except Exception:
        pass

    return out


# ── Scoring functions ────────────────────────────────────────────────────────

def _detect_base(prices: pd.Series, lookback: int = 60) -> dict:
    if len(prices) < lookback + 10:
        return {"base_length": 0, "is_breaking": False, "range_pct": 1.0}

    recent = prices.iloc[-lookback:]
    hi = recent.max()
    lo = recent.min()
    rng = (hi - lo) / lo if lo > 0 else 1.0

    is_tight_base = rng < 0.20
    last_close = prices.iloc[-1]
    pct_of_range = (last_close - lo) / (hi - lo) if (hi - lo) > 0 else 0
    is_breaking = pct_of_range > 0.85 and is_tight_base
    base_length = lookback if is_tight_base else 0

    return {"base_length": base_length, "is_breaking": is_breaking, "range_pct": rng}


def score_base_breakout(prices: pd.Series, volumes: pd.Series) -> float:
    base = _detect_base(prices)

    if not base["is_breaking"]:
        if base["base_length"] >= 40 and base["range_pct"] < 0.15:
            return 0.5
        if base["base_length"] >= 20:
            return 0.3
        return 0.1

    if len(volumes) < 20:
        return 0.6

    recent_vol = volumes.iloc[-5:].mean()
    base_vol = volumes.iloc[-60:-5].mean() if len(volumes) >= 65 else volumes.mean()
    vol_exp = recent_vol / base_vol if base_vol > 0 else 1.0

    if vol_exp >= 2.0:
        return 1.0
    if vol_exp >= 1.5:
        return 0.85
    if vol_exp >= 1.2:
        return 0.7
    return 0.55


def score_rsi_lift(rsi_series: pd.Series) -> float:
    if len(rsi_series) < 10:
        return 0.5

    rsi_now = rsi_series.iloc[-1]
    rsi_10d_ago = rsi_series.iloc[-10]
    rsi_rise = rsi_now - rsi_10d_ago

    if np.isnan(rsi_now) or np.isnan(rsi_10d_ago):
        return 0.3

    # Was oversold/neutral, now lifting — the early setup we want
    if rsi_10d_ago < 45 and rsi_now >= 50 and rsi_rise > 10:
        return 1.0
    if rsi_10d_ago < 50 and rsi_now >= 48 and rsi_rise > 5:
        return 0.85
    if rsi_now >= 45 and rsi_rise > 3:
        return 0.7
    if rsi_now >= 40 and rsi_rise > 0:
        return 0.5
    if rsi_now > 70:
        return 0.2  # too extended — momentum screen territory
    return 0.3


def score_fundamental_acceleration(sig: dict) -> float:
    score = 0.0
    weight_used = 0.0

    rev_qoq = sig.get("revenue_growth_qoq")
    if rev_qoq is not None:
        if rev_qoq > 0.20:
            score += 1.0
        elif rev_qoq > 0.10:
            score += 0.7
        elif rev_qoq > 0.0:
            score += 0.4
        else:
            score += 0.1
        weight_used += 1.0

    earn_qoq = sig.get("earnings_growth_qoq")
    if earn_qoq is not None:
        if earn_qoq > 0.30:
            score += 1.0
        elif earn_qoq > 0.10:
            score += 0.7
        elif earn_qoq > 0.0:
            score += 0.4
        else:
            score += 0.1
        weight_used += 1.0

    pio = sig.get("piotroski_score")
    if pio is not None:
        if pio >= 7:
            score += 1.0
        elif pio >= 5:
            score += 0.6
        elif pio >= 3:
            score += 0.3
        weight_used += 1.0

    if weight_used == 0:
        return 0.4  # no data — neutral, let technicals decide
    return score / weight_used


def score_analyst_catalyst(sig: dict, current_price: float) -> float:
    grade = sig.get("grade_consensus", "")
    pt_avg = sig.get("price_target_avg")

    if grade in ("Strong Buy", "Buy") and pt_avg and current_price > 0:
        upside = (pt_avg - current_price) / current_price
        if upside > 0.50:
            return 1.0
        if upside > 0.30:
            return 0.8
        if upside > 0.15:
            return 0.6
        return 0.4

    if pt_avg and current_price > 0:
        upside = (pt_avg - current_price) / current_price
        if upside > 0.40:
            return 0.9  # any grade with huge upside = undiscovered
        if upside > 0.20:
            return 0.6
        return 0.4

    if not grade:
        return 0.6  # no analyst coverage = potential hidden gem

    return 0.3


def score_market_cap_proxy(price: float, avg_vol: float) -> float:
    """Reward small/mid cap using daily dollar volume as proxy."""
    if price <= 0 or avg_vol <= 0:
        return 0.5

    dollar_vol = price * avg_vol

    if dollar_vol < 5_000_000:
        return 1.0   # micro cap — truly off radar
    if dollar_vol < 20_000_000:
        return 0.85  # small cap
    if dollar_vol < 100_000_000:
        return 0.6   # mid cap
    if dollar_vol < 500_000_000:
        return 0.3   # large cap
    return 0.1       # mega cap — everyone already knows it


# ── Core scoring ─────────────────────────────────────────────────────────────

def analyze_ticker(
    ticker: str,
    df: pd.DataFrame,
    sig: dict,
    snapshot: Optional[dict] = None,
) -> Optional[dict]:
    """Score a single ticker for up-and-comer potential. Returns None if filtered."""
    if len(df) < 60:
        return None

    close = df["close"]
    volume = df["volume"]
    last = close.iloc[-1]

    # 52w high/low — prefer Polygon snapshot value, fall back to bars
    hi_52w_snap = (snapshot or {}).get("hi_52w")
    if hi_52w_snap and hi_52w_snap > 0:
        hi_52w = hi_52w_snap
    else:
        hi_52w = close.iloc[-252:].max() if len(close) >= 252 else close.max()

    if hi_52w <= 0 or last <= 0:
        return None

    drawdown = (hi_52w - last) / hi_52w
    if drawdown < MIN_DRAWDOWN or drawdown > MAX_DRAWDOWN:
        return None

    rsi = compute_rsi(close)
    ema_s = compute_ema(close, EMA_SHORT)
    ema_l = compute_ema(close, EMA_LONG)
    vol_ratio = compute_volume_ratio(df)

    avg_vol_30 = volume.iloc[-30:].mean() if len(volume) >= 30 else volume.mean()

    s_base    = score_base_breakout(close, volume)
    s_rsi     = score_rsi_lift(rsi)
    s_fund    = score_fundamental_acceleration(sig)
    s_cap     = score_market_cap_proxy(last, avg_vol_30)
    s_analyst = score_analyst_catalyst(sig, last)
    s_vol     = min(1.0, vol_ratio / 2.0) if not np.isnan(vol_ratio) else 0.5

    # Sector heat — try sector_heat module, default neutral
    s_sector = 0.5
    try:
        from sector_heat import sector_score
        s_sector = sector_score(ticker)
    except Exception:
        try:
            from screen.sector_heat import sector_score
            s_sector = sector_score(ticker)
        except Exception:
            pass

    composite = (
        W_BASE_BREAKOUT  * s_base    +
        W_RSI_LIFT       * s_rsi     +
        W_FUNDAMENTAL    * s_fund    +
        W_SMALL_CAP      * s_cap     +
        W_SECTOR         * s_sector  +
        W_ANALYST        * s_analyst +
        W_VOLUME_ACCUM   * s_vol
    ) * 100

    notes = []
    if s_base >= 0.7:
        notes.append("base breakout")
    elif s_base >= 0.4:
        notes.append("coiling in base")
    if s_rsi >= 0.7:
        notes.append("RSI lifting")
    if s_fund >= 0.7:
        notes.append("accelerating fundamentals")
    if s_cap >= 0.85:
        notes.append("small/mid cap — off-radar")
    if s_analyst >= 0.7:
        notes.append("big upside to PT")
    if s_vol >= 0.7:
        notes.append("accumulation vol")

    rsi_val = rsi.iloc[-1] if len(rsi) > 0 else float("nan")
    ema_s_v = ema_s.iloc[-1] if len(ema_s) > 0 else float("nan")
    ema_l_v = ema_l.iloc[-1] if len(ema_l) > 0 else float("nan")

    return {
        "ticker": ticker,
        "score": round(composite, 1),
        "current_price": round(last, 2),
        "hi_52w": round(hi_52w, 2),
        "drawdown_pct": round(drawdown * 100, 1),
        "rsi": round(rsi_val, 1) if not np.isnan(rsi_val) else None,
        "ema_8": round(ema_s_v, 2) if not np.isnan(ema_s_v) else None,
        "ema_13": round(ema_l_v, 2) if not np.isnan(ema_l_v) else None,
        "vol_ratio": round(vol_ratio, 2) if not np.isnan(vol_ratio) else None,
        "revenue_growth_qoq": sig.get("revenue_growth_qoq"),
        "piotroski_score": sig.get("piotroski_score"),
        "grade_consensus": sig.get("grade_consensus", ""),
        "price_target_avg": sig.get("price_target_avg"),
        "rationale": "; ".join(notes) if notes else "early potential",
        "_s_base": round(s_base, 2),
        "_s_rsi": round(s_rsi, 2),
        "_s_fund": round(s_fund, 2),
        "_s_cap": round(s_cap, 2),
        "_s_analyst": round(s_analyst, 2),
    }


# ── Main screen entry point ───────────────────────────────────────────────────

def run_screen(
    min_score: float = 35.0,
    top_n: int = 10,
    single_ticker: Optional[str] = None,
) -> List[dict]:
    """
    Run the hidden gems / up-and-comer screen.

    Phase 1: Polygon snapshot of ALL ~10k US stocks → cheap filter to candidates.
    Phase 2: Fetch 300d daily bars from Polygon for each candidate → score.
    Phase 3: Enrich with Supabase fundamentals for tickers we have data on.

    Falls back to Supabase daily_prices universe if Polygon is unavailable.
    """
    # ── Single ticker shortcut ───────────────────────────────────────────────
    if single_ticker:
        ticker = single_ticker.upper()
        bars = _fetch_bars_polygon(ticker)
        df = _bars_to_df(bars)
        if df.empty:
            # Fall back to Supabase
            try:
                from reversal_screen import load_prices
                conn = _conn()
                frames = load_prices(conn, [ticker])
                df = frames.get(ticker, pd.DataFrame())
            except Exception:
                pass

        sig = {}
        try:
            conn = _conn()
            sig = _load_signal_data(conn, [ticker]).get(ticker, {})
        except Exception:
            pass

        result = analyze_ticker(ticker, df, sig)
        return [result] if result else []

    # ── Phase 1: Polygon snapshot of full US market ──────────────────────────
    print(f"[upcomer] Phase 1: fetching all US equity snapshots...", file=sys.stderr)
    snapshots = _fetch_all_snapshots()
    snap_map = {s["ticker"]: s for s in snapshots}

    if snapshots:
        candidates = _phase1_filter(snapshots)
        print(f"[upcomer] Phase 1: {len(snapshots)} tickers → {len(candidates)} candidates after drawdown filter", file=sys.stderr)
    else:
        # Polygon unavailable — fall back to Supabase daily_prices universe
        print("[upcomer] Polygon unavailable — falling back to Supabase universe", file=sys.stderr)
        candidates = []
        try:
            conn = _conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT ticker FROM daily_prices "
                    "WHERE trade_date >= CURRENT_DATE - INTERVAL '10 days'"
                )
                tickers_db = [r[0] for r in cur.fetchall()]
            candidates = [{"ticker": t, "drawdown_est": None} for t in tickers_db]
        except Exception as e:
            return [{"error": f"Data unavailable: {e}"}]

    # ── Phase 2: Historical bars + scoring ───────────────────────────────────
    # Sort candidates: prioritize those with confirmed drawdown in range
    confirmed = [c for c in candidates if c.get("drawdown_est") is not None]
    unconfirmed = [c for c in candidates if c.get("drawdown_est") is None]
    ordered = confirmed + unconfirmed

    # Cap at 600 for performance — already pre-filtered by drawdown
    ordered = ordered[:600]

    print(f"[upcomer] Phase 2: scoring {len(ordered)} candidates...", file=sys.stderr)

    # Load Supabase fundamentals for all candidates in one query
    all_tickers = [c["ticker"] for c in ordered]
    sigs: Dict[str, dict] = {}
    try:
        conn = _conn()
        sigs = _load_signal_data(conn, all_tickers)
    except Exception:
        sigs = {t: {} for t in all_tickers}

    results = []
    for cand in ordered:
        ticker = cand["ticker"]
        snap = snap_map.get(ticker)

        if snapshots:
            # Fetch bars from Polygon
            bars = _fetch_bars_polygon(ticker)
            df = _bars_to_df(bars)
        else:
            # Supabase fallback
            try:
                from reversal_screen import load_prices
                frames = load_prices(conn, [ticker])
                df = frames.get(ticker, pd.DataFrame())
            except Exception:
                df = pd.DataFrame()

        if df.empty or len(df) < 60:
            continue

        sig = sigs.get(ticker, {})
        result = analyze_ticker(ticker, df, sig, snapshot=snap)
        if result and result["score"] >= min_score:
            results.append(result)

        # Small delay to be a good API citizen (Polygon Starter = unlimited but rate-limited)
        if snapshots:
            time.sleep(0.05)

    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"[upcomer] Done. {len(results)} gems found above {min_score} score.", file=sys.stderr)
    return results[:top_n]


# ── CLI output ───────────────────────────────────────────────────────────────

def _print_results(results: List[dict]) -> None:
    if not results:
        print("No hidden gems found above threshold.")
        return

    print(f"\n{'─'*95}")
    print(f"  WATCHTOWER — HIDDEN GEMS / UP-AND-COMERS   ({date.today()})")
    print(f"{'─'*95}")
    print(f"  {'TICKER':<8} {'SCORE':>5}  {'PRICE':>7}  {'52wHi':>7}  {'DD%':>5}  "
          f"{'RSI':>5}  {'GRADE':<14}  RATIONALE")
    print(f"{'─'*95}")

    for r in results:
        grade = r.get("grade_consensus") or "—"
        pt = r.get("price_target_avg")
        grade_str = f"{grade[:8]} ${pt:.0f}" if pt else grade[:12]
        print(
            f"  {r['ticker']:<8} {r['score']:>5.0f}  "
            f"${r['current_price']:>6.2f}  "
            f"${r['hi_52w']:>6.2f}  "
            f"{r['drawdown_pct']:>4.0f}%  "
            f"{r.get('rsi') or 0:>5.1f}  "
            f"{grade_str:<16}  "
            f"{r.get('rationale', '')}"
        )
    print(f"{'─'*95}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watchtower Hidden Gems / Up-and-Comer screen")
    parser.add_argument("--min-score", type=float, default=35.0)
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--ticker", type=str, default=None)
    args = parser.parse_args()

    results = run_screen(
        min_score=args.min_score,
        top_n=args.top_n,
        single_ticker=args.ticker,
    )
    _print_results(results)
