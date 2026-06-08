"""
Watchtower — Intraday scan screen using Polygon live snapshots.

Detects intraday setups forming right now:

  BULLISH: GAP_AND_GO, INTRADAY_BREAKOUT, VWAP_BREAKOUT, FLUSH_REVERSAL, GAP_REVERSAL
  BEARISH: VWAP_REJECTION, INTRADAY_BREAKDOWN, GAP_DOWN_CONFIRM, DISTRIBUTION
  NEUTRAL: VOLUME_SURGE (directional unclear)

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


# ── Watchlist helpers ─────────────────────────────────────────────────────────

def _load_watchlist(conn) -> list:
    """
    Load active tickers from the optional `watchlist` table.
    Returns empty list if the table doesn't exist or any error occurs.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM watchlist WHERE active = true")
            rows = cur.fetchall()
        return [row[0] for row in rows if row[0]]
    except Exception:
        return []


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
    premarket: bool = False,
) -> tuple:
    """
    Returns (signal_type: str, score: float, rationale: str).

    Bullish: GAP_AND_GO, INTRADAY_BREAKOUT, VWAP_BREAKOUT, FLUSH_REVERSAL, GAP_REVERSAL
    Bearish: VWAP_REJECTION, INTRADAY_BREAKDOWN, GAP_DOWN_CONFIRM, DISTRIBUTION
    Neutral: VOLUME_SURGE

    premarket=True loosens vol pace (1.5x→1.2x) and gap (3%→2%) thresholds
    since pre-market volume is structurally lower.
    """
    at_hod = today_high > 0 and (today_high - current_price) / today_high <= 0.005
    at_lod = today_low > 0 and (current_price - today_low) / today_low <= 0.005

    # Pre-market uses lower thresholds — volume is structurally thin before 9:30
    vol_thresh = 1.2 if premarket else 1.5
    gap_thresh = 2.0 if premarket else 3.0
    gap_vol_thresh = 1.5 if premarket else 2.0

    # ── BULLISH ───────────────────────────────────────────────────────────────

    # 1. GAP_AND_GO — gapped up, still holding, volume confirming
    if (gap_pct >= gap_thresh
            and prev_close > 0
            and (current_price - prev_close) >= 0.5 * (today_high - prev_close)
            and vol_pace_ratio >= gap_vol_thresh):
        score = min(100, 55 + gap_pct * 2 + (vol_pace_ratio - gap_vol_thresh) * 5)
        return "GAP_AND_GO", score, f"Gapped +{gap_pct:.1f}%, holding with {vol_pace_ratio:.1f}x volume pace"

    # 2. INTRADAY_BREAKOUT — at HOD above VWAP with volume
    if above_vwap and at_hod and vol_pace_ratio >= vol_thresh and change_pct > 0:
        score = min(100, 50 + (vol_pace_ratio - vol_thresh) * 8 + change_pct * 2)
        return "INTRADAY_BREAKOUT", score, f"At HOD above VWAP, {vol_pace_ratio:.1f}x volume pace"

    # 3. VWAP_BREAKOUT — above VWAP, not at HOD yet
    if above_vwap and not at_hod and vol_pace_ratio >= vol_thresh and change_pct > 0:
        score = min(100, 45 + (vol_pace_ratio - vol_thresh) * 6)
        return "VWAP_BREAKOUT", score, f"Above VWAP with {vol_pace_ratio:.1f}x volume pace"

    # 4. FLUSH_REVERSAL — flushed hard, now back above VWAP
    if (change_pct < 0 and above_vwap
            and prev_close > 0 and today_low < prev_close * 0.97
            and vol_pace_ratio >= vol_thresh):
        score = min(100, 45 + (vol_pace_ratio - vol_thresh) * 6 + abs(change_pct))
        return "FLUSH_REVERSAL", score, "Flushed to lows, now reclaiming VWAP"

    # 5. GAP_REVERSAL — gapped down, recovering above VWAP
    if (gap_pct <= -gap_thresh and change_pct > gap_pct * 0.5
            and above_vwap and vol_pace_ratio >= vol_thresh):
        score = min(100, 50 + (vol_pace_ratio - vol_thresh) * 5)
        return "GAP_REVERSAL", score, f"Gapped down {gap_pct:.1f}%, recovering above VWAP"

    # ── BEARISH ───────────────────────────────────────────────────────────────

    # 6. VWAP_REJECTION — rallied to VWAP, got rejected, fading below with volume
    if (not above_vwap and change_pct < 0
            and prev_close > 0 and today_high >= prev_close * 0.99
            and vol_pace_ratio >= vol_thresh):
        score = min(100, 45 + (vol_pace_ratio - vol_thresh) * 6 + abs(change_pct))
        return "VWAP_REJECTION", score, f"Rejected at VWAP, fading {change_pct:.1f}% on {vol_pace_ratio:.1f}x volume"

    # 7. INTRADAY_BREAKDOWN — at LOD below VWAP with volume
    if (not above_vwap and at_lod
            and vol_pace_ratio >= vol_thresh and change_pct < -1.0):
        score = min(100, 50 + (vol_pace_ratio - vol_thresh) * 8 + abs(change_pct) * 2)
        return "INTRADAY_BREAKDOWN", score, f"At LOD below VWAP, {vol_pace_ratio:.1f}x volume — breakdown"

    # 8. GAP_DOWN_CONFIRM — gapped down, failing to recover VWAP, bearish continuation
    if (gap_pct <= -gap_thresh and not above_vwap
            and change_pct <= gap_pct * 0.5
            and vol_pace_ratio >= vol_thresh):
        score = min(100, 55 + abs(gap_pct) * 1.5 + (vol_pace_ratio - vol_thresh) * 5)
        return "GAP_DOWN_CONFIRM", score, f"Gapped down {gap_pct:.1f}%, failing to recover — bears in control"

    # 9. DISTRIBUTION — near HOD but heavy volume on down candles (proxy: high vol, negative change)
    if (at_hod and not above_vwap and vol_pace_ratio >= (1.5 if premarket else 2.0) and change_pct < -0.5):
        score = min(100, 45 + (vol_pace_ratio - (1.5 if premarket else 2.0)) * 6)
        return "DISTRIBUTION", score, f"High volume selling near HOD — distribution signal"

    # ── NEUTRAL ───────────────────────────────────────────────────────────────

    # 10. VOLUME_SURGE — something is happening, direction unclear
    if vol_pace_ratio >= 3.0:
        score = min(100, 40 + (vol_pace_ratio - 3.0) * 5)
        return "VOLUME_SURGE", score, f"Volume at {vol_pace_ratio:.1f}x pace — unusual activity"

    return "NEUTRAL", 0.0, ""


# ── Main screen logic ─────────────────────────────────────────────────────────

def run_screen(
    min_score: float = 35.0,
    single_ticker: str = None,
    broad: bool = True,
) -> List[dict]:
    """
    Run intraday scan using Polygon snapshots + Supabase baseline volumes.

    Args:
        min_score: Minimum score to include in results (default 35).
        single_ticker: If set, scan only this ticker (bypasses universe filter).
        broad: If True (default), fetch ALL stocks via Polygon snapshot and
               filter by minimum dollar volume ($500k today). This covers the
               full US market — not just the quality 40 or watchlist.
               Set False to restrict to quality universe + watchlist only.

    Returns:
        List of result dicts sorted by score descending.
    """
    # ── Load Supabase fundamentals / prices ───────────────────────────────────
    try:
        conn = _conn()
    except Exception:
        conn = None

    quality_map = {}
    watchlist_tickers = []
    if conn is not None:
        try:
            quality_map = load_quality_tickers(conn)  # already a Dict[ticker -> dict]
        except Exception:
            pass
        try:
            watchlist_tickers = _load_watchlist(conn)
        except Exception:
            pass

    # ── Polygon client ────────────────────────────────────────────────────────
    client = get_client()
    if client is None:
        return [{"error": "POLYGON_API_KEY not configured"}]

    # ── Market time ───────────────────────────────────────────────────────────
    minutes_elapsed, is_market_hours = _market_minutes_elapsed()

    # ── Determine ticker universe + snapshots ─────────────────────────────────
    BATCH_SIZE = 200
    snapshots: dict = {}  # ticker -> snap object
    avg_vol_map: dict = {}  # ticker -> 20d avg volume

    if single_ticker:
        # ── Single ticker mode ────────────────────────────────────────────────
        universe = [single_ticker.upper()]
        prices_df = pd.DataFrame()
        if conn is not None:
            try:
                prices_df = load_prices(conn, universe, days=30)
            except Exception:
                pass
        if not prices_df.empty and "ticker" in prices_df.columns and "volume" in prices_df.columns:
            grp = prices_df.groupby("ticker")["volume"].apply(lambda s: s.tail(20).mean())
            avg_vol_map = grp.to_dict()

        for i in range(0, len(universe), BATCH_SIZE):
            batch = universe[i : i + BATCH_SIZE]
            try:
                snaps = client.get_snapshot_all("stocks", params={"tickers": ",".join(batch)})
                for snap in (snaps or []):
                    if hasattr(snap, "ticker") and snap.ticker:
                        snapshots[snap.ticker] = snap
            except Exception:
                pass

    elif broad:
        # ── Broad mode: full US market via Polygon snapshot ───────────────────
        # No artificial ticker cap — filter by minimum dollar volume instead.
        # $500k today = liquid enough to trade, cuts noise from micro/nano junk.
        MIN_DOLLAR_VOL = 500_000
        try:
            all_snaps = client.get_snapshot_all("stocks", include_otc=False)
        except Exception:
            all_snaps = []

        for snap in (all_snaps or []):
            try:
                ticker = getattr(snap, "ticker", None)
                if not ticker or len(ticker) > 5:
                    continue
                day = getattr(snap, "day", None)
                if not day:
                    continue
                price = getattr(day, "c", 0) or 0
                vol = getattr(day, "v", 0) or 0
                if price * vol < MIN_DOLLAR_VOL:
                    continue
                snapshots[ticker] = snap
            except Exception:
                continue

        universe = list(snapshots.keys())

        # Load 20d avg volumes from Supabase for known tickers
        prices_df = pd.DataFrame()
        if conn is not None and universe:
            try:
                prices_df = load_prices(conn, universe, days=30)
            except Exception:
                pass
        if not prices_df.empty and "ticker" in prices_df.columns and "volume" in prices_df.columns:
            grp = prices_df.groupby("ticker")["volume"].apply(lambda s: s.tail(20).mean())
            avg_vol_map = grp.to_dict()

        # Fall back to prevDay.v for tickers not in DB
        for ticker, snap in snapshots.items():
            if ticker not in avg_vol_map:
                try:
                    prev_vol = getattr(snap.prevDay, "v", 0) or 0
                    if prev_vol > 0:
                        avg_vol_map[ticker] = float(prev_vol)
                except Exception:
                    pass

    else:
        # ── Default mode: quality universe + watchlist ─────────────────────────
        quality_tickers = list(quality_map.keys())
        combined = list(dict.fromkeys(quality_tickers + watchlist_tickers))  # deduped, order-preserving
        universe = combined

        prices_df = pd.DataFrame()
        if conn is not None and universe:
            try:
                prices_df = load_prices(conn, universe, days=30)
            except Exception:
                pass
        if not prices_df.empty and "ticker" in prices_df.columns and "volume" in prices_df.columns:
            grp = prices_df.groupby("ticker")["volume"].apply(lambda s: s.tail(20).mean())
            avg_vol_map = grp.to_dict()

        for i in range(0, len(universe), BATCH_SIZE):
            batch = universe[i : i + BATCH_SIZE]
            try:
                snaps = client.get_snapshot_all("stocks", params={"tickers": ",".join(batch)})
                for snap in (snaps or []):
                    if hasattr(snap, "ticker") and snap.ticker:
                        snapshots[snap.ticker] = snap
            except Exception:
                pass

    if not universe:
        return []

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
                premarket=not is_market_hours,
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
