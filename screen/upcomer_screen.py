"""
Watchtower — Hidden Gems / Up-and-Comer screen.

Completely separate from momentum_screen.py. Hunts for stocks most investors
have NOT yet put on their radar — diamonds in the rough.

Data strategy (two-phase to handle ~10k tickers efficiently):
  Phase 1 — Polygon snapshot of ALL US equities in one API call.
             Cheap price/volume/drawdown filters cut to ~300-600 candidates.
  Phase 2 — Fetch 300d daily bars from Polygon for each candidate.
             Score base breakout, RSI lift, volume accumulation.
  Phase 3 — Enrich with Supabase fundamentals (financial_scores,
             analyst_revisions) for tickers we have data on.

Scoring model (sum to 100):
  - Base breakout (long consolidation + volume expansion):   25 pts
  - RSI lift from oversold/neutral:                         20 pts
  - Fundamental acceleration (rev/earnings QoQ):            20 pts
  - Small/mid cap bonus (dollar volume proxy):              10 pts
  - Sector heat / emerging tailwind:                        10 pts
  - Analyst catalyst (big PT upside or uncovered):          10 pts
  - Volume accumulation (up-vol / down-vol ratio):           5 pts

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
from typing import Dict, List, Optional

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
    EMA_SHORT, EMA_LONG, RSI_PERIOD,
)

# ── Config ───────────────────────────────────────────────────────────────────
MIN_DRAWDOWN = 0.18    # at least 18% off 52w high — not a momentum name
MAX_DRAWDOWN = 0.60    # not a completely destroyed stock
MIN_PRICE    = 2.0     # filter out penny stocks
MAX_PRICE    = 500.0   # keep small/mid focused
MIN_AVG_VOL  = 50_000  # minimum daily volume (liquidity floor)
MAX_CANDIDATES = 600   # cap Phase 2 for performance

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
    Returns pre-filtered list of candidate dicts.
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
                if not ticker or len(ticker) > 5:
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
                if price < MIN_PRICE or price > MAX_PRICE:
                    continue
                if volume < MIN_AVG_VOL:
                    continue

                change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

                max_52w = getattr(s, "max", None)
                min_52w = getattr(s, "min", None)
                hi_52w = getattr(max_52w, "price", None) if max_52w else None
                lo_52w = getattr(min_52w, "price", None) if min_52w else None

                # Drawdown pre-filter using snapshot data
                drawdown_est = None
                if hi_52w and hi_52w > 0:
                    drawdown_est = (hi_52w - price) / hi_52w
                    if drawdown_est < MIN_DRAWDOWN or drawdown_est > MAX_DRAWDOWN:
                        continue

                candidates.append({
                    "ticker": ticker,
                    "price": price,
                    "volume": volume,
                    "change_pct": change_pct,
                    "hi_52w": hi_52w,
                    "lo_52w": lo_52w,
                    "prev_close": prev_close,
                    "drawdown_est": drawdown_est,
                })
            except Exception:
                continue
    except Exception as e:
        print(f"[upcomer] Snapshot fetch error: {e}", file=sys.stderr)

    return candidates


def _fetch_bars_polygon(ticker: str, days: int = 300) -> pd.DataFrame:
    """Fetch daily bars from Polygon and return as DataFrame."""
    client = _get_polygon_client()
    if not client:
        return pd.DataFrame()
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
        if not aggs:
            return pd.DataFrame()
        rows = []
        for a in aggs:
            rows.append({
                "trade_date": date.fromtimestamp(a.timestamp / 1000),
                "close": float(a.close),
                "volume": float(a.volume),
                "high": float(a.high),
                "low": float(a.low),
                "open": float(a.open),
            })
        df = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


# ── Sector heat (computed once, looked up per ticker) ────────────────────────

def _build_sector_heat_map(candidates: List[dict], ticker_sector_map: Dict[str, str]) -> Dict[str, float]:
    """
    Compute sector heat scores from snapshot data.
    Groups candidates by sector using ticker→sector map from Supabase.
    Returns dict of sector → heat score (0-1).
    """
    sector_prices: Dict[str, List[float]] = {}
    sector_volumes: Dict[str, List[float]] = {}

    for c in candidates:
        ticker = c["ticker"]
        sector = ticker_sector_map.get(ticker, "Unknown")
        change_pct = c.get("change_pct", 0.0) or 0.0
        volume = c.get("volume", 0.0) or 0.0

        sector_prices.setdefault(sector, []).append(change_pct)
        sector_volumes.setdefault(sector, []).append(volume)

    heat_map: Dict[str, float] = {}
    for sector, changes in sector_prices.items():
        if len(changes) < 3:
            continue
        avg_change = float(np.mean(changes))
        # Normalize: >3% avg daily gain = very hot, flat = neutral
        if avg_change > 3.0:
            heat = 1.0
        elif avg_change > 1.5:
            heat = 0.8
        elif avg_change > 0.5:
            heat = 0.6
        elif avg_change > 0.0:
            heat = 0.5
        elif avg_change > -1.0:
            heat = 0.35
        else:
            heat = 0.2
        heat_map[sector] = round(heat, 3)

    return heat_map


def _load_ticker_sector_map(conn, tickers: List[str]) -> Dict[str, str]:
    """Load ticker → sector mapping from Supabase tickers table."""
    out: Dict[str, str] = {}
    if not tickers or not conn:
        return out
    placeholders = ",".join(["%s"] * len(tickers))
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT ticker, sector FROM tickers WHERE ticker IN ({placeholders})",
                tickers,
            )
            for row in cur.fetchall():
                out[row[0]] = row[1] or "Unknown"
    except Exception:
        pass
    return out


# ── Supabase fundamental enrichment ─────────────────────────────────────────

def _load_signal_data(conn, tickers: List[str]) -> Dict[str, dict]:
    out: Dict[str, dict] = {t: {} for t in tickers}
    if not tickers or not conn:
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
    """Detect long consolidation base and whether price is breaking out of it."""
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
    """Score 0-1: long base + volume expansion on breakout."""
    base = _detect_base(prices)

    if not base["is_breaking"]:
        if base["base_length"] >= 40 and base["range_pct"] < 0.15:
            return 0.5   # coiling tight — breakout potential
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
    """
    Score 0-1: RSI lifting from oversold/neutral into momentum.
    Sweet spot: was below 45, now rising toward 50-65.
    NOT overbought (>70) — that belongs on the momentum screen.
    """
    if len(rsi_series) < 10:
        return 0.5

    rsi_now = rsi_series.iloc[-1]
    rsi_10d = rsi_series.iloc[-10]
    rsi_rise = rsi_now - rsi_10d

    if np.isnan(rsi_now) or np.isnan(rsi_10d):
        return 0.3

    if rsi_10d < 45 and rsi_now >= 50 and rsi_rise > 10:
        return 1.0   # was oversold, now lifting strongly — ideal early setup
    if rsi_10d < 50 and rsi_now >= 48 and rsi_rise > 5:
        return 0.85
    if rsi_now >= 45 and rsi_rise > 3:
        return 0.7
    if rsi_now >= 40 and rsi_rise > 0:
        return 0.5
    if rsi_now > 70:
        return 0.2   # too extended — momentum screen territory
    return 0.3


def score_fundamental_acceleration(sig: dict) -> float:
    """Score 0-1: reward QoQ revenue/earnings acceleration and financial health."""
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
        return 0.4  # no data — neutral, let technicals carry the score
    return score / weight_used


def score_analyst_catalyst(sig: dict, current_price: float) -> float:
    """
    Score 0-1: big upside to analyst PT = still undiscovered.
    No analyst coverage at all = also a potential hidden gem signal.
    """
    grade = sig.get("grade_consensus", "")
    pt_avg = sig.get("price_target_avg")

    if pt_avg and current_price > 0:
        upside = (pt_avg - current_price) / current_price
        if grade in ("Strong Buy", "Buy"):
            if upside > 0.50:
                return 1.0
            if upside > 0.30:
                return 0.8
            if upside > 0.15:
                return 0.6
            return 0.4
        else:
            # Any grade with huge upside = analyst hasn't caught on yet
            if upside > 0.50:
                return 0.9
            if upside > 0.30:
                return 0.7
            if upside > 0.15:
                return 0.5
            return 0.3

    if not grade and not pt_avg:
        return 0.6  # no analyst coverage = potentially undiscovered

    return 0.3


def score_market_cap_proxy(price: float, avg_vol: float) -> float:
    """
    Score 0-1: reward small/mid cap using daily dollar volume as proxy.
    Less covered = more room to run = higher score.
    """
    if price <= 0 or avg_vol <= 0:
        return 0.5

    dollar_vol = price * avg_vol

    if dollar_vol < 5_000_000:
        return 1.0    # micro cap — truly off radar
    if dollar_vol < 20_000_000:
        return 0.85   # small cap
    if dollar_vol < 100_000_000:
        return 0.6    # mid cap
    if dollar_vol < 500_000_000:
        return 0.3    # large cap — already on everyone's screen
    return 0.1        # mega cap — no edge here


# ── Core ticker scorer ───────────────────────────────────────────────────────

def analyze_ticker(
    ticker: str,
    df: pd.DataFrame,
    sig: dict,
    sector_heat_score: float = 0.5,
    snapshot: Optional[dict] = None,
) -> Optional[dict]:
    """Score a single ticker for up-and-comer potential. Returns None if filtered out."""
    if df.empty or len(df) < 60:
        return None

    close = df["close"]
    volume = df["volume"]
    last = close.iloc[-1]

    # 52w high — prefer Polygon snapshot value (more accurate), fall back to bars
    hi_52w_snap = (snapshot or {}).get("hi_52w")
    hi_52w = hi_52w_snap if (hi_52w_snap and hi_52w_snap > 0) else (
        close.iloc[-252:].max() if len(close) >= 252 else close.max()
    )

    if hi_52w <= 0 or last <= 0:
        return None

    drawdown = (hi_52w - last) / hi_52w
    if drawdown < MIN_DRAWDOWN or drawdown > MAX_DRAWDOWN:
        return None

    rsi = compute_rsi(close)
    ema_s = compute_ema(close, EMA_SHORT)
    ema_l = compute_ema(close, EMA_LONG)

    # volume_ratio needs close + volume columns — both present in Polygon bars df
    vol_ratio = compute_volume_ratio(df)
    avg_vol_30 = volume.iloc[-30:].mean() if len(volume) >= 30 else volume.mean()

    s_base    = score_base_breakout(close, volume)
    s_rsi     = score_rsi_lift(rsi)
    s_fund    = score_fundamental_acceleration(sig)
    s_cap     = score_market_cap_proxy(last, avg_vol_30)
    s_sector  = sector_heat_score
    s_analyst = score_analyst_catalyst(sig, last)
    s_vol     = min(1.0, vol_ratio / 2.0) if not np.isnan(vol_ratio) else 0.5

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
        notes.append("small/mid — off-radar")
    if s_analyst >= 0.7:
        notes.append("big PT upside")
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
        "sector": sig.get("sector", ""),
        "rationale": "; ".join(notes) if notes else "early potential",
        "_s_base": round(s_base, 2),
        "_s_rsi": round(s_rsi, 2),
        "_s_fund": round(s_fund, 2),
        "_s_cap": round(s_cap, 2),
        "_s_sector": round(s_sector, 2),
        "_s_analyst": round(s_analyst, 2),
    }


# ── Main screen entry point ───────────────────────────────────────────────────

def run_screen(
    min_score: float = 35.0,
    top_n: int = 10,
    single_ticker: Optional[str] = None,
    with_synthesis: bool = False,
) -> List[dict]:
    """
    Run the hidden gems / up-and-comer screen against the full US market.

    Phase 1: Polygon snapshot all ~10k US equities → cheap filter → ~300-600 candidates.
    Phase 2: 300d daily bars from Polygon per candidate → score.
    Phase 3: Supabase fundamentals + sector map enrichment.
    Phase 4 (optional): Grok synthesis narrative.

    Falls back to Supabase daily_prices universe if Polygon unavailable.
    """
    conn = None
    try:
        conn = _conn()
    except Exception:
        pass

    # ── Single ticker shortcut ───────────────────────────────────────────────
    if single_ticker:
        ticker = single_ticker.upper()
        df = _fetch_bars_polygon(ticker)

        if df.empty and conn:
            try:
                from reversal_screen import load_prices
                frames = load_prices(conn, [ticker])
                df = frames.get(ticker, pd.DataFrame())
            except Exception:
                pass

        sig = {}
        sector_map = {}
        if conn:
            sig = _load_signal_data(conn, [ticker]).get(ticker, {})
            sector_map = _load_ticker_sector_map(conn, [ticker])
            sig["sector"] = sector_map.get(ticker, "")

        result = analyze_ticker(ticker, df, sig, sector_heat_score=0.5)
        results = [result] if result else []

        if with_synthesis and results:
            results = _synthesize(results)
        return results

    # ── Phase 1: Polygon full market snapshot ────────────────────────────────
    print("[upcomer] Phase 1: fetching all US equity snapshots...", file=sys.stderr)
    candidates = _fetch_all_snapshots()
    snap_map = {c["ticker"]: c for c in candidates}
    use_polygon = bool(candidates)

    if use_polygon:
        print(f"[upcomer] Phase 1 complete: {len(candidates)} candidates after pre-filter", file=sys.stderr)
    else:
        print("[upcomer] Polygon unavailable — falling back to Supabase universe", file=sys.stderr)
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT ticker FROM daily_prices "
                        "WHERE trade_date >= CURRENT_DATE - INTERVAL '10 days'"
                    )
                    db_tickers = [r[0] for r in cur.fetchall()]
                candidates = [{"ticker": t, "drawdown_est": None} for t in db_tickers]
                snap_map = {}
            except Exception as e:
                return [{"error": f"Data unavailable: {e}"}]

    # Cap and sort — confirmed drawdown candidates first
    confirmed = [c for c in candidates if c.get("drawdown_est") is not None]
    unconfirmed = [c for c in candidates if c.get("drawdown_est") is None]
    ordered = (confirmed + unconfirmed)[:MAX_CANDIDATES]
    all_tickers = [c["ticker"] for c in ordered]

    # ── Phase 3 prep: load fundamentals + sector map in bulk ─────────────────
    sigs: Dict[str, dict] = {t: {} for t in all_tickers}
    sector_map: Dict[str, str] = {}
    if conn:
        try:
            sigs = _load_signal_data(conn, all_tickers)
        except Exception:
            pass
        try:
            sector_map = _load_ticker_sector_map(conn, all_tickers)
            for t in all_tickers:
                sigs[t]["sector"] = sector_map.get(t, "Unknown")
        except Exception:
            pass

    # Build sector heat map from snapshot data + sector assignments
    sector_heat_map = _build_sector_heat_map(candidates, sector_map)

    # ── Phase 2: historical bars + scoring ───────────────────────────────────
    print(f"[upcomer] Phase 2: scoring {len(ordered)} candidates...", file=sys.stderr)
    results = []

    for cand in ordered:
        ticker = cand["ticker"]
        snap = snap_map.get(ticker)

        if use_polygon:
            df = _fetch_bars_polygon(ticker)
        else:
            df = pd.DataFrame()
            if conn:
                try:
                    from reversal_screen import load_prices
                    frames = load_prices(conn, [ticker])
                    df = frames.get(ticker, pd.DataFrame())
                except Exception:
                    pass

        if df.empty or len(df) < 60:
            continue

        sig = sigs.get(ticker, {})
        ticker_sector = sector_map.get(ticker, "Unknown")
        sector_heat = sector_heat_map.get(ticker_sector, 0.5)

        result = analyze_ticker(ticker, df, sig,
                                sector_heat_score=sector_heat,
                                snapshot=snap)
        if result and result["score"] >= min_score:
            results.append(result)

        if use_polygon:
            time.sleep(0.05)  # be a good Polygon API citizen

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:top_n]
    print(f"[upcomer] Done. {len(results)} gems found above score {min_score}.", file=sys.stderr)

    # ── Phase 4: optional Grok synthesis ────────────────────────────────────
    if with_synthesis and results:
        results = _synthesize(results)

    return results


def _synthesize(results: List[dict]) -> List[dict]:
    """Add Grok narrative synthesis to the result set."""
    try:
        from analysis.grok_synthesizer import synthesize_screen_results
        narrative = synthesize_screen_results("upcomer", results, len(results))
        if narrative:
            results[0]["synthesis"] = narrative
    except Exception:
        pass
    return results


# ── CLI output ───────────────────────────────────────────────────────────────

def _print_results(results: List[dict]) -> None:
    if not results:
        print("No hidden gems found above threshold.")
        return

    print(f"\n{'─'*100}")
    print(f"  WATCHTOWER — HIDDEN GEMS / UP-AND-COMERS   ({date.today()})")
    print(f"{'─'*100}")
    print(f"  {'TICKER':<8} {'SCORE':>5}  {'PRICE':>7}  {'52wHi':>7}  {'DD%':>5}  "
          f"{'RSI':>5}  {'SECTOR':<18}  RATIONALE")
    print(f"{'─'*100}")

    for r in results:
        sector = (r.get("sector") or "")[:16]
        pt = r.get("price_target_avg")
        pt_str = f" PT${pt:.0f}" if pt else ""
        print(
            f"  {r['ticker']:<8} {r['score']:>5.0f}  "
            f"${r['current_price']:>6.2f}  "
            f"${r['hi_52w']:>6.2f}  "
            f"{r['drawdown_pct']:>4.0f}%  "
            f"{r.get('rsi') or 0:>5.1f}  "
            f"{sector:<18}  "
            f"{r.get('rationale', '')}{pt_str}"
        )

    if results[0].get("synthesis"):
        print(f"\n{'─'*100}")
        print("GROK SYNTHESIS:")
        print(results[0]["synthesis"])

    print(f"{'─'*100}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watchtower Hidden Gems / Up-and-Comer screen")
    parser.add_argument("--min-score", type=float, default=35.0)
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--ticker", type=str, default=None)
    parser.add_argument("--with-synthesis", action="store_true")
    args = parser.parse_args()

    results = run_screen(
        min_score=args.min_score,
        top_n=args.top_n,
        single_ticker=args.ticker,
        with_synthesis=args.with_synthesis,
    )
    _print_results(results)
