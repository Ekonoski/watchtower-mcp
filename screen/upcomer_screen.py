"""
Watchtower — Hidden Gems / Up-and-Comer screen.

Completely separate from momentum_screen.py. This is NOT "strong getting stronger" —
it hunts for stocks that most investors have NOT yet put on their radar:
  - Meaningful pullback from highs (20-50%) — not near 52w highs (that's momentum)
  - Signs of fundamental acceleration: QoQ revenue/earnings growth trends improving
  - Breaking out of long consolidation bases on above-average volume
  - Small/mid cap bias — large caps are already on everyone's radar
  - Emerging sector tailwinds
  - Low analyst coverage or recent upgrade from obscurity
  - RSI lifting from oversold/neutral — early, not extended

Scoring model (sum to 100):
  - Base breakout (long consolidation + volume expansion):   25 pts
  - RSI lift from low / momentum starting:                  20 pts
  - Fundamental acceleration (rev/earnings trend):          20 pts
  - Small/mid cap bonus (market cap proxy):                 10 pts
  - Sector heat / tailwind emerging:                        10 pts
  - Analyst upgrade catalyst (low → higher consensus):      10 pts
  - Volume accumulation (up-vol / down-vol):                 5 pts

Usage:
    set -a && source .env && set +a
    python3 screen/upcomer_screen.py
    python3 screen/upcomer_screen.py --ticker ACMR
    python3 screen/upcomer_screen.py --broad
"""
import argparse
import sys
from datetime import date
from typing import Dict, List, Optional

try:
    import numpy as np
    import pandas as pd
except ImportError as e:
    print(f"ERROR: missing dependency: {e}. Run: pip install pandas numpy", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from reversal_screen import (
    _conn,
    load_quality_tickers,
    load_prices,
    compute_rsi,
    compute_ema,
    compute_macd,
    compute_volume_ratio,
    compute_volume_surge,
    compute_spy_regime,
    EMA_SHORT, EMA_LONG, EMA_TREND, RSI_PERIOD,
)
try:
    from reversal_screen import _patch_polygon_snapshots
except ImportError:
    _patch_polygon_snapshots = lambda x: x

try:
    from screen.sector_heat import sector_score
except Exception:
    try:
        from sector_heat import sector_score
    except Exception:
        sector_score = lambda ticker: 0.5


# ── Scoring weights ─────────────────────────────────────────────────────────
W_BASE_BREAKOUT  = 0.25
W_RSI_LIFT       = 0.20
W_FUNDAMENTAL    = 0.20
W_SMALL_CAP      = 0.10
W_SECTOR         = 0.10
W_ANALYST        = 0.10
W_VOLUME_ACCUM   = 0.05

# Min drawdown from 52w high — upcomer must be off highs (not a momentum name)
MIN_DRAWDOWN = 0.18   # at least 18% off high
MAX_DRAWDOWN = 0.60   # but not a destroyed stock


def _load_signal_data(conn, tickers: List[str]) -> Dict[str, dict]:
    """Load analyst and fundamental data from Supabase for the ticker list."""
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
                SELECT DISTINCT ON (ticker) ticker, piotroski_score, altman_z_score,
                       revenue_growth_qoq, revenue_growth_yoy, earnings_growth_qoq
                FROM financial_scores
                WHERE ticker IN ({placeholders})
                ORDER BY ticker, as_of_date DESC
                """,
                tickers,
            )
            for row in cur.fetchall():
                t, pio, altman, rev_qoq, rev_yoy, earn_qoq = row
                out[t]["piotroski_score"] = pio
                out[t]["altman_z_score"] = float(altman) if altman else None
                out[t]["revenue_growth_qoq"] = float(rev_qoq) if rev_qoq else None
                out[t]["revenue_growth_yoy"] = float(rev_yoy) if rev_yoy else None
                out[t]["earnings_growth_qoq"] = float(earn_qoq) if earn_qoq else None
    except Exception:
        pass

    return out


def _detect_base(prices: pd.Series, lookback: int = 60) -> dict:
    """
    Detect a long consolidation base followed by potential breakout.

    Returns:
      base_length  — consecutive days in a tight range (low vol/price range)
      is_breaking  — True if recent close > upper bound of base
      vol_expansion — current vol vs base-period vol
    """
    if len(prices) < lookback + 10:
        return {"base_length": 0, "is_breaking": False, "vol_expansion": 1.0}

    recent = prices.iloc[-lookback:]
    hi = recent.max()
    lo = recent.min()
    rng = (hi - lo) / lo if lo > 0 else 0

    # Tight base = price range < 20% over the lookback window
    is_tight_base = rng < 0.20

    # Breaking out = latest close is in top 5% of the base range
    last_close = prices.iloc[-1]
    pct_of_range = (last_close - lo) / (hi - lo) if (hi - lo) > 0 else 0
    is_breaking = pct_of_range > 0.85 and is_tight_base

    # Base length = how many days since price was first in this range
    base_length = lookback if is_tight_base else 0

    return {
        "base_length": base_length,
        "is_breaking": is_breaking,
        "range_pct": rng,
    }


def score_base_breakout(prices: pd.Series, volumes: pd.Series) -> float:
    """Score 0-1: reward long base + breakout + volume expansion."""
    base = _detect_base(prices)

    if not base["is_breaking"]:
        # Partial credit for tight base (coiling)
        if base["base_length"] >= 40 and base["range_pct"] < 0.15:
            return 0.5
        if base["base_length"] >= 20:
            return 0.3
        return 0.1

    # Volume expansion on breakout
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
    Score 0-1: reward RSI lifting from low/neutral territory into momentum.

    Upcomer sweet spot: RSI was below 45, now rising toward 50-65.
    NOT overbought (>70) — that's momentum screen territory.
    """
    if len(rsi_series) < 10:
        return 0.5

    rsi_now = rsi_series.iloc[-1]
    rsi_10d_ago = rsi_series.iloc[-10]
    rsi_rise = rsi_now - rsi_10d_ago

    if np.isnan(rsi_now) or np.isnan(rsi_10d_ago):
        return 0.3

    # Ideal: was oversold/neutral, now lifting
    if rsi_10d_ago < 45 and rsi_now >= 50 and rsi_rise > 10:
        return 1.0
    if rsi_10d_ago < 50 and rsi_now >= 48 and rsi_rise > 5:
        return 0.85
    if rsi_now >= 45 and rsi_rise > 3:
        return 0.7
    if rsi_now >= 40 and rsi_rise > 0:
        return 0.5
    if rsi_now > 70:
        return 0.2  # too extended for an upcomer — momentum screen territory
    return 0.3


def score_fundamental_acceleration(sig: dict) -> float:
    """
    Score 0-1: reward fundamental improvement trajectory.

    Looks for: positive QoQ revenue trend, improving earnings, healthy Piotroski.
    """
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
        else:
            score += 0.0
        weight_used += 1.0

    if weight_used == 0:
        return 0.4  # no data — neutral
    return score / weight_used


def score_analyst_catalyst(sig: dict, current_price: float) -> float:
    """
    Score 0-1: reward situations where analyst coverage is low or improving.

    Hidden gems: small price target upside remaining = already discovered.
    Big upside to price target = still undiscovered / undervalued.
    """
    grade = sig.get("grade_consensus", "")
    pt_avg = sig.get("price_target_avg")

    if grade in ("Strong Buy", "Buy") and pt_avg and current_price > 0:
        upside = (pt_avg - current_price) / current_price
        if upside > 0.50:
            return 1.0   # massive upside — still early
        if upside > 0.30:
            return 0.8
        if upside > 0.15:
            return 0.6
        if upside > 0.0:
            return 0.4
        return 0.2

    if grade in ("Hold", "Neutral", "") and pt_avg and current_price > 0:
        upside = (pt_avg - current_price) / current_price
        # "Hold" with big upside = analyst hasn't caught on yet — hidden gem
        if upside > 0.40:
            return 0.9
        if upside > 0.20:
            return 0.6
        return 0.4

    if not grade:
        return 0.5  # no analyst coverage = potential hidden gem

    return 0.3


def score_market_cap_proxy(price: float, avg_vol: float) -> float:
    """
    Score 0-1: reward small/mid cap (proxy by price * 30-day avg volume / 1M).

    We don't have exact shares outstanding, so use dollar volume as proxy.
    Lower dollar volume = smaller, less covered = bigger upside potential.
    """
    if price <= 0 or avg_vol <= 0:
        return 0.5

    dollar_vol_30d = price * avg_vol  # rough daily $ volume

    if dollar_vol_30d < 5_000_000:      # micro/nano cap
        return 1.0
    if dollar_vol_30d < 20_000_000:     # small cap
        return 0.85
    if dollar_vol_30d < 100_000_000:    # mid cap
        return 0.6
    if dollar_vol_30d < 500_000_000:    # large cap — less hidden
        return 0.3
    return 0.1  # mega cap — definitely on everyone's radar


def analyze_ticker(
    ticker: str,
    df: pd.DataFrame,
    sig: dict,
    sector_heat_score: float = 0.5,
) -> Optional[dict]:
    """Score a single ticker for up-and-comer potential. Returns None if filtered."""

    if len(df) < 60:
        return None

    close = df["close"]
    volume = df["volume"]

    hi_52w = close.iloc[-252:].max() if len(close) >= 252 else close.max()
    lo_52w = close.iloc[-252:].min() if len(close) >= 252 else close.min()
    last = close.iloc[-1]

    if hi_52w <= 0 or last <= 0:
        return None

    drawdown = (hi_52w - last) / hi_52w

    # Must be meaningfully off highs — not near 52w high (that's momentum)
    if drawdown < MIN_DRAWDOWN or drawdown > MAX_DRAWDOWN:
        return None

    rsi = compute_rsi(close)
    ema_s = compute_ema(close, EMA_SHORT)
    ema_l = compute_ema(close, EMA_LONG)
    _, _, macd_hist = compute_macd(close)
    vol_ratio = compute_volume_ratio(df)

    # Composite score
    s_base     = score_base_breakout(close, volume)
    s_rsi      = score_rsi_lift(rsi)
    s_fund     = score_fundamental_acceleration(sig)
    s_cap      = score_market_cap_proxy(last, volume.iloc[-30:].mean())
    s_sector   = sector_heat_score
    s_analyst  = score_analyst_catalyst(sig, last)
    s_vol      = min(1.0, vol_ratio / 2.0) if not np.isnan(vol_ratio) else 0.5

    composite = (
        W_BASE_BREAKOUT * s_base +
        W_RSI_LIFT      * s_rsi  +
        W_FUNDAMENTAL   * s_fund +
        W_SMALL_CAP     * s_cap  +
        W_SECTOR        * s_sector +
        W_ANALYST       * s_analyst +
        W_VOLUME_ACCUM  * s_vol
    ) * 100

    # Build rationale
    notes = []
    if s_base >= 0.7:
        notes.append("base breakout")
    elif s_base >= 0.4:
        notes.append("coiling in base")
    if s_rsi >= 0.7:
        notes.append("RSI lifting from low")
    if s_fund >= 0.7:
        notes.append("accelerating fundamentals")
    if s_cap >= 0.7:
        notes.append("small/mid cap — off-radar")
    if s_analyst >= 0.7:
        notes.append("big upside to PT")
    if s_vol >= 0.7:
        notes.append("accumulation volume")

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
        # Component scores for transparency
        "_s_base": round(s_base, 2),
        "_s_rsi": round(s_rsi, 2),
        "_s_fund": round(s_fund, 2),
        "_s_cap": round(s_cap, 2),
        "_s_sector": round(s_sector, 2),
        "_s_analyst": round(s_analyst, 2),
    }


def run_screen(
    min_score: float = 35.0,
    top_n: int = 10,
    broad: bool = False,
    single_ticker: Optional[str] = None,
) -> List[dict]:
    """
    Run the up-and-comer / hidden gems screen.

    Args:
        min_score: Minimum composite score (0-100) to include.
        top_n: Max results to return (sorted by score).
        broad: If True, include all tickers in DB (not just quality universe).
        single_ticker: If set, score only this ticker regardless of universe.

    Returns:
        List of result dicts, sorted by score descending.
    """
    try:
        conn = _conn()
    except Exception as e:
        return [{"error": f"DB connection failed: {e}"}]

    # Universe
    if single_ticker:
        tickers = [single_ticker.upper()]
    elif broad:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT ticker FROM daily_prices WHERE date >= CURRENT_DATE - INTERVAL '10 days'")
                tickers = [r[0] for r in cur.fetchall()]
        except Exception:
            tickers = load_quality_tickers(conn)
    else:
        # quality universe + watchlist
        tickers = load_quality_tickers(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT ticker FROM watchlist WHERE active = TRUE")
                watchlist = [r[0] for r in cur.fetchall()]
            tickers = list(set(tickers + watchlist))
        except Exception:
            pass

    # Load price history
    frames = load_prices(conn, tickers, lookback_days=400)

    # Polygon live patch
    try:
        frames = _patch_polygon_snapshots(frames)
    except Exception:
        pass

    # Load fundamental/analyst signals
    sigs = _load_signal_data(conn, tickers)

    # Score each ticker
    results = []
    for ticker in tickers:
        df = frames.get(ticker)
        if df is None or df.empty:
            continue
        sig = sigs.get(ticker, {})
        try:
            sh = sector_score(ticker)
        except Exception:
            sh = 0.5

        result = analyze_ticker(ticker, df, sig, sector_heat_score=sh)
        if result and result["score"] >= min_score:
            results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


def _print_results(results: List[dict]) -> None:
    if not results:
        print("No up-and-comers found above threshold.")
        return

    print(f"\n{'─'*90}")
    print(f"  WATCHTOWER — HIDDEN GEMS / UP-AND-COMERS   ({date.today()})")
    print(f"{'─'*90}")
    print(f"  {'TICKER':<8} {'SCORE':>5}  {'PRICE':>7}  {'52wHi':>7}  {'DD%':>5}  "
          f"{'RSI':>5}  {'GRADE':<12}  RATIONALE")
    print(f"{'─'*90}")

    for r in results:
        grade = r.get("grade_consensus") or "—"
        pt = r.get("price_target_avg")
        grade_str = f"{grade[:8]} ${pt:.0f}" if pt else grade[:10]
        print(
            f"  {r['ticker']:<8} {r['score']:>5.0f}  "
            f"${r['current_price']:>6.2f}  "
            f"${r['hi_52w']:>6.2f}  "
            f"{r['drawdown_pct']:>4.0f}%  "
            f"{r.get('rsi') or 0:>5.1f}  "
            f"{grade_str:<14}  "
            f"{r.get('rationale', '')}"
        )
    print(f"{'─'*90}\n")


# ── CLI entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watchtower Hidden Gems / Up-and-Comer screen")
    parser.add_argument("--min-score", type=float, default=35.0)
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--broad", action="store_true")
    parser.add_argument("--ticker", type=str, default=None)
    args = parser.parse_args()

    results = run_screen(
        min_score=args.min_score,
        top_n=args.top_n,
        broad=args.broad,
        single_ticker=args.ticker,
    )
    _print_results(results)
