"""
Watchtower — Intraday scan screen using Polygon live snapshots.

Detects intraday setups forming right now:
  GAP_AND_GO, INTRADAY_BREAKOUT, VWAP_BREAKOUT, FLUSH_REVERSAL, GAP_REVERSAL, VOLUME_SURGE

Usage:
    set -a && source .env && set +a
    python3 screen/intraday_screen.py
    python3 screen/intraday_screen.py --ticker ONDS
    python3 screen/intraday_screen.py --top 20 --min-score 40
"""
import argparse
import sys
from datetime import datetime
from typing import List, Optional

try:
    import pytz
    _HAS_PYTZ = True
except ImportError:
    _HAS_PYTZ = False

try:
    import pandas as pd
except ImportError as e:
    print(f"ERROR: missing dependency: {e}.  Run:  pip install pandas", file=sys.stderr)
    sys.exit(1)

from screen.reversal_screen import _conn, load_quality_tickers, load_prices
from analysis.polygon_data import get_client


# ── Market time helpers ───────────────────────────────────────────────────────

def _get_et_now():
    """Return current datetime in ET. Falls back to UTC if pytz not available."""
    if _HAS_PYTZ:
        et_tz = pytz.timezone("America/New_York")
        return datetime.now(et_tz)
    return datetime.utcnow()


def _market_minutes_elapsed() -> tuple:
    """
    Returns (minutes_elapsed, is_market_hours).
    minutes_elapsed = minutes since 9:30 AM ET today (capped at 390 = full session).
    is_market_hours = True if currently between 9:30 and 16:00 ET.
    """
    now = _get_et_now()
    open_minutes = 9 * 60 + 30    # 570
    close_minutes = 16 * 60 + 0   # 960
    current_minutes = now.hour * 60 + now.minute

    is_market_hours = open_minutes <= current_minutes <= close_minutes

    if current_minutes < open_minutes:
        minutes_elapsed = 0
    elif current_minutes > close_minutes:
        minutes_elapsed = 390
    else:
        minutes_elapsed = current_minutes - open_minutes

    return minutes_elapsed, is_market_hours


# ── Signal classification ─────────────────────────────────────────────────────

def classify_intraday(
    gap_pct: float,
    change_pct: float,
    vol_pace_ratio: float,
    above_vwap: bool,
    today_high: float,
    current_price: float,
    today_low: float,
    prev_close: float,
) -> tuple:
    """
    Returns (signal_type: str, score: float, rationale: str).
    signal_type is one of: GAP_AND_GO, INTRADAY_BREAKOUT, VWAP_BREAKOUT,
    FLUSH_REVERSAL, GAP_REVERSAL, VOLUME_SURGE, NEUTRAL.
    """
    # 1. GAP_AND_GO
    if (gap_pct >= 3.0
            and prev_close > 0
            and (current_price - prev_close) >= 0.5 * (today_high - prev_close)
            and vol_pace_ratio >= 2.0):
        score = min(100, 55 + gap_pct * 2 + (vol_pace_ratio - 2.0) * 5)
        rationale = f"Gapped +{gap_pct:.1f}%, holding with {vol_pace_ratio:.1f}x volume pace"
        return "GAP_AND_GO", score, rationale

    # 2. INTRADAY_BREAKOUT — at HOD above VWAP
    at_hod = today_high > 0 and (today_high - current_price) / today_high <= 0.005
    if (above_vwap and at_hod and vol_pace_ratio >= 1.5 and change_pct > 0):
        score = min(100, 50 + (vol_pace_ratio - 1.5) * 8 + change_pct * 2)
        rationale = f"At HOD above VWAP, {vol_pace_ratio:.1f}x volume pace"
        return "INTRADAY_BREAKOUT", score, rationale

    # 3. VWAP_BREAKOUT — above VWAP, not at HOD
    if (above_vwap and not at_hod and vol_pace_ratio >= 1.5 and change_pct > 0):
        score = min(100, 45 + (vol_pace_ratio - 1.5) * 6)
        rationale = f"Above VWAP with {vol_pace_ratio:.1f}x volume pace"
        return "VWAP_BREAKOUT", score, rationale

    # 4. FLUSH_REVERSAL — was down hard, now reclaiming VWAP
    if (change_pct < 0
            and above_vwap
            and prev_close > 0
            and today_low < prev_close * 0.97
            and vol_pace_ratio >= 1.5):
        score = min(100, 45 + (vol_pace_ratio - 1.5) * 6 + abs(change_pct))
        rationale = "Flushed to lows, now reclaiming VWAP"
        return "FLUSH_REVERSAL", score, rationale

    # 5. GAP_REVERSAL — gapped down, recovering above VWAP
    if (gap_pct <= -3.0
            and change_pct > gap_pct * 0.5
            and above_vwap
            and vol_pace_ratio >= 1.5):
        score = min(100, 50 + (vol_pace_ratio - 1.5) * 5)
        rationale = f"Gapped down {gap_pct:.1f}%, recovering above VWAP"
        return "GAP_REVERSAL", score, rationale

    # 6. VOLUME_SURGE — unusual activity, no directional setup
    if vol_pace_ratio >= 3.0:
        score = min(100, 40 + (vol_pace_ratio - 3.0) * 5)
        rationale = f"Volume at {vol_pace_ratio:.1f}x pace — unusual activity"
        return "VOLUME_SURGE", score, rationale

    return "NEUTRAL", 0.0, ""


# ── Main screen logic ─────────────────────────────────────────────────────────

def run_screen(
    min_score: float = 35.0,
    single_ticker: str = None,
    broad: bool = False,
) -> List[dict]:
    """
    Run intraday scan using Polygon snapshots + Supabase baseline volumes.

    Args:
        min_score: Minimum score to include in results (default 35).
        single_ticker: If set, scan only this ticker (bypasses quality universe filter).
        broad: If True, use all quality universe tickers; otherwise standard set.

    Returns:
        List of result dicts sorted by score descending.
    """
    # ── Load Supabase fundamentals / prices ───────────────────────────────────
    try:
        conn = _conn()
    except Exception:
        conn = None

    quality_rows = []
    quality_map = {}
    if conn is not None:
        try:
            quality_rows = load_quality_tickers(conn)
            quality_map = {r["ticker"]: r for r in quality_rows}
        except Exception:
            pass

    prices_df = pd.DataFrame()
    if conn is not None:
        try:
            if single_ticker:
                prices_df = load_prices(conn, [single_ticker.upper()], days=30)
            else:
                tickers_to_load = [r["ticker"] for r in quality_rows]
                if tickers_to_load:
                    prices_df = load_prices(conn, tickers_to_load, days=30)
        except Exception:
            pass

    # Compute 20-day avg volume per ticker
    avg_vol_map: dict = {}
    if not prices_df.empty and "ticker" in prices_df.columns and "volume" in prices_df.columns:
        grp = prices_df.groupby("ticker")["volume"].apply(
            lambda s: s.tail(20).mean()
        )
        avg_vol_map = grp.to_dict()

    # ── Polygon client ────────────────────────────────────────────────────────
    client = get_client()
    if client is None:
        return [{"error": "POLYGON_API_KEY not configured"}]

    # ── Market time ───────────────────────────────────────────────────────────
    minutes_elapsed, is_market_hours = _market_minutes_elapsed()

    # ── Determine ticker universe ─────────────────────────────────────────────
    if single_ticker:
        universe = [single_ticker.upper()]
    else:
        universe = [r["ticker"] for r in quality_rows]

    if not universe:
        return []

    # ── Fetch snapshots in batches of 200 ────────────────────────────────────
    BATCH_SIZE = 200
    snapshots: dict = {}  # ticker -> snap object

    for i in range(0, len(universe), BATCH_SIZE):
        batch = universe[i : i + BATCH_SIZE]
        try:
            snaps = client.get_snapshot_all("stocks", params={"tickers": ",".join(batch)})
            for snap in (snaps or []):
                if hasattr(snap, "ticker") and snap.ticker:
                    snapshots[snap.ticker] = snap
        except Exception:
            pass

    # ── Score each ticker ─────────────────────────────────────────────────────
    results = []

    for ticker in universe:
        snap = snapshots.get(ticker)
        if snap is None:
            continue

        try:
            day = snap.day
            prev_day = snap.prevDay

            today_open = getattr(day, "o", None) or 0.0
            today_high = getattr(day, "h", None) or 0.0
            today_low = getattr(day, "l", None) or 0.0
            current_price = getattr(day, "c", None) or 0.0
            today_volume = getattr(day, "v", None) or 0.0
            vwap = getattr(day, "vw", None) or 0.0

            prev_close = getattr(prev_day, "c", None) or 0.0

            # Derived metrics
            gap_pct = ((today_open - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
            change_pct = getattr(snap, "todaysChangePerc", None)
            if change_pct is None:
                change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

            avg_vol = avg_vol_map.get(ticker, 0.0)

            # Volume pace ratio
            if avg_vol > 0 and today_volume > 0:
                volume_pace = (today_volume / max(minutes_elapsed, 1)) * 390
                vol_pace_ratio = volume_pace / avg_vol
            else:
                vol_pace_ratio = 0.0

            above_vwap = (current_price > vwap) if vwap > 0 else False

            signal_type, score, rationale = classify_intraday(
                gap_pct=gap_pct,
                change_pct=change_pct,
                vol_pace_ratio=vol_pace_ratio,
                above_vwap=above_vwap,
                today_high=today_high,
                current_price=current_price,
                today_low=today_low,
                prev_close=prev_close,
            )

            if signal_type == "NEUTRAL" or score < min_score:
                continue

            # Pull quality fundamentals if available
            q = quality_map.get(ticker, {})

            result = {
                "ticker": ticker,
                "sleeve": "intraday",
                "signal_type": signal_type,
                "score": round(score, 1),
                "rationale": rationale,
                "current_price": current_price,
                "vwap": vwap,
                "today_open": today_open,
                "prev_close": prev_close,
                "gap_pct": round(gap_pct, 2),
                "change_pct": round(change_pct, 2),
                "vol_pace_ratio": round(vol_pace_ratio, 2),
                "today_volume": int(today_volume),
                "above_vwap": above_vwap,
                "minutes_elapsed": minutes_elapsed,
                "is_market_hours": is_market_hours,
                # Fundamentals (may be empty for non-universe tickers)
                "company_name": q.get("company_name", ""),
                "sector": q.get("sector", ""),
            }
            results.append(result)

        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ── CLI helpers ───────────────────────────────────────────────────────────────

def print_summary(results: List[dict]) -> None:
    if not results:
        print("No intraday setups found above threshold.")
        return

    first = results[0]
    if first.get("error"):
        print(f"Error: {first['error']}")
        return

    is_mkt = first.get("is_market_hours", True)
    mins = first.get("minutes_elapsed", 0)
    if is_mkt:
        print(f"{'='*90}")
        print(f"INTRADAY SCAN — {mins} min into session")
        print(f"{'='*90}")
    else:
        print(f"{'='*90}")
        print(f"INTRADAY SCAN — Market closed (showing last session snapshot data)")
        print(f"{'='*90}")

    hdr = f"{'Ticker':<8} {'Signal':<20} {'Score':>5} {'Chg%':>7} {'VolPace':>8} {'VWAP':>5} {'Price':>8}  Rationale"
    print(hdr)
    print("-" * 95)
    for r in results:
        vwap_flag = "↑" if r.get("above_vwap") else "↓"
        print(
            f"{r['ticker']:<8} {r['signal_type']:<20} {r['score']:>5.0f} "
            f"{r['change_pct']:>+6.1f}% {r['vol_pace_ratio']:>7.1f}x {vwap_flag}VWAP "
            f"${r['current_price']:>7.2f}  {r['rationale']}"
        )


def main():
    parser = argparse.ArgumentParser(description="Watchtower intraday scan")
    parser.add_argument("--top", type=int, default=20, help="Number of results to show")
    parser.add_argument("--ticker", type=str, default=None, help="Single ticker to scan")
    parser.add_argument("--broad", action="store_true", help="Use broad universe")
    parser.add_argument("--min-score", type=float, default=35.0, help="Minimum score filter")
    args = parser.parse_args()

    results = run_screen(
        min_score=args.min_score,
        single_ticker=args.ticker.upper() if args.ticker else None,
        broad=args.broad,
    )
    print_summary(results[: args.top])


if __name__ == "__main__":
    main()
