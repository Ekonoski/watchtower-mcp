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
import os
import sys
from datetime import datetime
from typing import List, Optional

# Polygon Starter tier delivers snapshots ~15 min delayed. Volume-pace math
# must use the data's timestamp, not the wall clock, or pace is understated
# all day and reads ~0 for the first 15 min of the session (killing nearly
# every signal in the prime morning window). Set to 0 on a real-time plan.
DATA_DELAY_MIN = int(os.environ.get("POLYGON_DATA_DELAY_MIN", "15"))

# A signal must be tradeable: require this much dollar volume traded TODAY
# before a ticker can appear in scan results (single-ticker lookups exempt).
MIN_SIGNAL_DOLLAR_VOL = float(os.environ.get("MIN_SIGNAL_DOLLAR_VOL", "250000"))

# Baseline sanity for the broad scan: new listings and recycled ticker
# symbols carry junk prevDay data (EROC debuted ~$19 with a phantom $1.74
# "prior close" → fake +1055% GAP_AND_GO). Require a real prior session
# and reject absurd computed moves.
MIN_PREV_DOLLAR_VOL = float(os.environ.get("MIN_PREV_DOLLAR_VOL", "50000"))
MAX_SANE_CHANGE_PCT = float(os.environ.get("MAX_SANE_CHANGE_PCT", "400"))

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


def _attr(obj, *names, default=None):
    """Get the first present attribute. The Polygon REST JSON uses camelCase
    (prevDay, min.av, lastTrade.p) but the Python client's dataclasses use
    snake_case (prev_day, min.accumulated_volume, last_trade.price) — code
    written against one silently reads None on the other."""
    if obj is None:
        return default
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default



def _load_avg_vols(conn, tickers: list) -> dict:
    """20-day average volume per ticker from daily_prices.

    load_prices returns Dict[ticker -> DataFrame] — earlier code treated it
    as one DataFrame and crashed on .empty ('dict' object has no attribute
    'empty'), so DB average volumes never actually loaded anywhere."""
    if conn is None or not tickers:
        return {}
    try:
        prices_map = load_prices(conn, tickers, days=40)
    except Exception:
        return {}
    out = {}
    for t, df in (prices_map or {}).items():
        try:
            if df is not None and not df.empty and "volume" in df.columns:
                v = float(df["volume"].tail(20).mean())
                if v > 0:
                    out[t] = v
        except Exception:
            continue
    return out


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


def _load_ticker_meta(conn, tickers: list) -> dict:
    """company_name + sector from the broad `tickers` universe. Fallback so the
    scanner can label names that pass the gap/volume filters but aren't in the
    curated quality set (most live gappers). Tickers not in our universe at all
    (obscure micro-caps, fresh listings) still come back blank — we have no
    sector for them without a live profile fetch."""
    if conn is None or not tickers:
        return {}
    out: dict = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, company_name, sector FROM tickers WHERE ticker = ANY(%s)",
                (list(tickers),),
            )
            for tk, cn, sec in cur.fetchall():
                out[tk] = {"company_name": cn, "sector": sec}
    except Exception:
        pass
    return out


_FMP_BASE = "https://financialmodelingprep.com/stable"


def _fetch_profile(ticker: str) -> dict:
    """One-shot FMP /profile → name/sector/industry for an off-universe gapper."""
    import logging
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    if not api_key:
        return {}
    try:
        import requests
        resp = requests.get(f"{_FMP_BASE}/profile",
                            params={"symbol": ticker, "apikey": api_key}, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            p = data[0]
            return {"company_name": (p.get("companyName") or "").strip() or None,
                    "sector": (p.get("sector") or "").strip() or None,
                    "industry": (p.get("industry") or "").strip() or None}
    except Exception as e:
        logging.getLogger(__name__).warning(f"[intraday_screen] profile fetch {ticker} failed: {e}")
    return {}


def _enrich_missing_sectors(conn, results: list, max_fetch: int = 80,
                            workers: int = 8, retry_after_hours: int = 12) -> None:
    """Fill sector/name for result rows still blank after the tickers fallback.
    Cache hits (gapper_profile_cache) are used first; the rest are fetched live
    from FMP in parallel (capped), then upserted. FMP misses are cached too so a
    persistently-unknown symbol doesn't burn the per-scan budget every scan — but
    they're retried after retry_after_hours. In-place mutation of `results`."""
    import logging
    from concurrent.futures import ThreadPoolExecutor
    log = logging.getLogger(__name__)
    missing = [r for r in results if not r.get("sector")]
    if not missing or conn is None:
        return
    tks = list({r["ticker"] for r in missing})
    cache: dict = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS gapper_profile_cache ("
                "ticker text PRIMARY KEY, company_name text, sector text, "
                "industry text, fetched_at timestamptz DEFAULT now())")
            conn.commit()
            cur.execute(
                "SELECT ticker, company_name, sector, "
                "(fetched_at < now() - %s * interval '1 hour') AS stale "
                "FROM gapper_profile_cache WHERE ticker = ANY(%s)",
                (retry_after_hours, tks))
            for tk, cn, sec, stale in cur.fetchall():
                cache[tk] = {"company_name": cn, "sector": sec, "stale": stale}
    except Exception as e:
        log.warning(f"[intraday_screen] gapper cache read failed: {e}")

    # Fill from cache hits first.
    for r in missing:
        m = cache.get(r["ticker"])
        if m and m.get("sector"):
            r["sector"] = m["sector"]
            if not r.get("company_name") and m.get("company_name"):
                r["company_name"] = m["company_name"]

    # Still blank → live fetch if never cached, or a stale miss.
    to_fetch = []
    for r in missing:
        if r.get("sector"):
            continue
        m = cache.get(r["ticker"])
        if m is None or (m.get("sector") is None and m.get("stale")):
            to_fetch.append(r["ticker"])
    to_fetch = list(dict.fromkeys(to_fetch))[:max_fetch]
    if not to_fetch:
        return

    # Network-bound → fetch concurrently, then upsert serially (psycopg2 conn is
    # not thread-safe). Cache every attempt, hits and misses alike.
    fetched: dict = {}
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for tk, meta in zip(to_fetch, ex.map(_fetch_profile, to_fetch)):
                fetched[tk] = meta or {}
    except Exception as e:
        log.warning(f"[intraday_screen] parallel profile fetch failed: {e}")
    try:
        with conn.cursor() as cur:
            for tk, meta in fetched.items():
                cur.execute(
                    "INSERT INTO gapper_profile_cache (ticker, company_name, sector, industry) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (ticker) DO UPDATE SET "
                    "company_name=EXCLUDED.company_name, sector=EXCLUDED.sector, "
                    "industry=EXCLUDED.industry, fetched_at=now()",
                    (tk, meta.get("company_name"), meta.get("sector"), meta.get("industry")))
        conn.commit()
    except Exception as e:
        log.warning(f"[intraday_screen] gapper cache write failed: {e}")

    for r in missing:
        if r.get("sector"):
            continue
        meta = fetched.get(r["ticker"])
        if meta and meta.get("sector"):
            r["sector"] = meta["sector"]
            if not r.get("company_name") and meta.get("company_name"):
                r["company_name"] = meta["company_name"]


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

    # Cap pace's contribution to SCORING (display keeps the real ratio).
    # Thin names print 100-2500x ratios that pegged every score at 100,
    # destroying the ranking — a 6x cap keeps scores spread across 40-95.
    pace = min(vol_pace_ratio, 6.0)

    # Magnitude floors: a "breakout" that hasn't moved isn't a breakout.
    # IBTL (bond ETF, +0.02% on a 10-cent day range) scored 84 because
    # at-HOD/above-VWAP are trivially true when the whole day is flat.
    day_range_pct = ((today_high - today_low) / current_price * 100) if (current_price > 0 and today_high > 0 and today_low > 0) else 0.0
    min_move = 0.5 if premarket else 1.0   # min |change %| for directional signals
    min_range = 1.0                        # min day range % for HOD/LOD signals

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
        score = min(100, 55 + gap_pct * 2 + (pace - gap_vol_thresh) * 5)
        return "GAP_AND_GO", score, f"Gapped +{gap_pct:.1f}%, holding with {vol_pace_ratio:.1f}x volume pace"

    # 2. INTRADAY_BREAKOUT — at HOD above VWAP with volume
    if (above_vwap and at_hod and vol_pace_ratio >= vol_thresh
            and change_pct >= min_move and day_range_pct >= min_range):
        score = min(100, 50 + (pace - vol_thresh) * 8 + change_pct * 2)
        return "INTRADAY_BREAKOUT", score, f"At HOD above VWAP, {vol_pace_ratio:.1f}x volume pace"

    # 3. VWAP_BREAKOUT — above VWAP, not at HOD yet
    # (calibrated 6/10: at 1.5x/1% this fired ~33/scan; 2x/2% targets ~10)
    if (above_vwap and not at_hod
            and vol_pace_ratio >= (1.5 if premarket else 2.0)
            and change_pct >= (1.0 if premarket else 2.0)):
        score = min(100, 40 + (pace - vol_thresh) * 6 + change_pct)
        return "VWAP_BREAKOUT", score, f"Above VWAP with {vol_pace_ratio:.1f}x volume pace"

    # 4. FLUSH_REVERSAL — flushed hard, now back above VWAP
    # (today_low > 0 guard: pre-market has no session low yet)
    if (change_pct < 0 and above_vwap
            and prev_close > 0 and 0 < today_low < prev_close * 0.97
            and vol_pace_ratio >= (1.5 if premarket else 2.0)):
        score = min(100, 45 + (pace - vol_thresh) * 6 + abs(change_pct))
        return "FLUSH_REVERSAL", score, "Flushed to lows, now reclaiming VWAP"

    # 5. GAP_REVERSAL — gapped down, recovering above VWAP
    if (gap_pct <= -gap_thresh and change_pct > gap_pct * 0.5
            and above_vwap and vol_pace_ratio >= vol_thresh):
        score = min(100, 50 + (pace - vol_thresh) * 5)
        return "GAP_REVERSAL", score, f"Gapped down {gap_pct:.1f}%, recovering above VWAP"

    # ── BEARISH ───────────────────────────────────────────────────────────────

    # 6. VWAP_REJECTION — rallied to VWAP, got rejected, fading below with volume
    # (calibrated 6/10: at 1.5x/-1% this was the #1 noise source, ~120/scan —
    # every fading stock qualifies late-day; 2x/-2% targets the real rejections)
    if (not above_vwap and change_pct <= -(1.0 if premarket else 2.0)
            and prev_close > 0 and today_high >= prev_close * 0.99
            and vol_pace_ratio >= (1.5 if premarket else 2.0)):
        score = min(100, 40 + (pace - vol_thresh) * 6 + abs(change_pct))
        return "VWAP_REJECTION", score, f"Rejected at VWAP, fading {change_pct:.1f}% on {vol_pace_ratio:.1f}x volume"

    # 7. INTRADAY_BREAKDOWN — at LOD below VWAP with volume
    if (not above_vwap and at_lod and day_range_pct >= min_range
            and vol_pace_ratio >= vol_thresh and change_pct < -1.0):
        score = min(100, 50 + (pace - vol_thresh) * 8 + abs(change_pct) * 2)
        return "INTRADAY_BREAKDOWN", score, f"At LOD below VWAP, {vol_pace_ratio:.1f}x volume — breakdown"

    # 8. GAP_DOWN_CONFIRM — gapped down, failing to recover VWAP, bearish continuation
    if (gap_pct <= -gap_thresh and not above_vwap
            and change_pct <= gap_pct * 0.5
            and vol_pace_ratio >= vol_thresh):
        score = min(100, 55 + abs(gap_pct) * 1.5 + (pace - vol_thresh) * 5)
        return "GAP_DOWN_CONFIRM", score, f"Gapped down {gap_pct:.1f}%, failing to recover — bears in control"

    # 9. DISTRIBUTION — near HOD but heavy volume on down candles (proxy: high vol, negative change)
    if (at_hod and not above_vwap and vol_pace_ratio >= (1.5 if premarket else 2.0) and change_pct < -0.5):
        score = min(100, 45 + (pace - (1.5 if premarket else 2.0)) * 6)
        return "DISTRIBUTION", score, f"High volume selling near HOD — distribution signal"

    # ── NEUTRAL ───────────────────────────────────────────────────────────────

    # 10. VOLUME_SURGE — something is happening, direction unclear
    # (calibrated 6/10: 3x fired ~50/scan; 4x + a real move keeps it WATCH-worthy)
    if vol_pace_ratio >= 4.0 and abs(change_pct) >= 1.0:
        score = min(100, 40 + (pace - 4.0) * 5 + abs(change_pct))
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
    # Owns the DB connection for the whole scan; the finally guarantees it's
    # released on every return/raise path. This runs every 5 minutes during
    # market hours AND on every dashboard drawer open — leaking here exhausts
    # the Supabase pool and every screen starts failing as "no data".
    try:
        conn = _conn()
    except Exception:
        conn = None
    try:
        return _run_screen_with_conn(conn, min_score, single_ticker, broad)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _run_screen_with_conn(
    conn,
    min_score: float,
    single_ticker: str,
    broad: bool,
) -> List[dict]:
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
        avg_vol_map = _load_avg_vols(conn, universe)

        for i in range(0, len(universe), BATCH_SIZE):
            batch = universe[i : i + BATCH_SIZE]
            try:
                # tickers must be a direct kwarg — a params dict is ignored and
                # the call lazily fetches the FULL market (same bug fixed in the
                # news scanner). list() materializes so HTTP errors surface here.
                snaps = list(client.get_snapshot_all("stocks", tickers=",".join(batch)))
                for snap in snaps:
                    if hasattr(snap, "ticker") and snap.ticker:
                        snapshots[snap.ticker] = snap
            except Exception:
                pass

    elif broad:
        # ── Broad mode: full US market via Polygon snapshot ───────────────────
        # Min price $1 filters true penny/shell stocks. Min dollar vol $50k
        # keeps the universe wide — catches small-cap runners and biotech movers.
        MIN_PRICE = 1.0
        MIN_DOLLAR_VOL = 50_000
        import logging as _logging
        _log = _logging.getLogger(__name__)
        try:
            # Force-materialize the iterator inside try/except — get_snapshot_all
            # is lazy and Polygon HTTP errors surface during iteration, not at call time.
            all_snaps = list(client.get_snapshot_all("stocks", include_otc=False))
            _log.info(f"[intraday_screen] Polygon returned {len(all_snaps)} raw snapshots.")
        except Exception as e:
            _log.warning(f"[intraday_screen] Polygon get_snapshot_all failed: {e}")
            all_snaps = []

        for snap in all_snaps:
            try:
                ticker = getattr(snap, "ticker", None)
                if not ticker or len(ticker) > 5:
                    continue
                # Pre-market the delayed feed has no `day` bar yet — fall back
                # to the latest minute bar (carries pre-market price/volume)
                # and prevDay so the universe doesn't collapse to zero.
                day = getattr(snap, "day", None)
                minute = getattr(snap, "min", None)
                prev = _attr(snap, "prevDay", "prev_day")
                price = _attr(day, "c", "close", default=0) or 0
                if not price:
                    price = _attr(minute, "c", "close", default=0) or 0
                vol = _attr(day, "v", "volume", default=0) or 0
                if not vol:
                    vol = _attr(minute, "av", "accumulated_volume", default=0) or 0
                prev_price = _attr(prev, "c", "close", default=0) or 0
                # Use best available price for filtering
                effective_price = price or prev_price
                if effective_price < MIN_PRICE:
                    continue
                # Pre-market: today's volume is near-zero, use prev day to check liquidity
                if price * vol < MIN_DOLLAR_VOL:
                    prev_vol = _attr(prev, "v", "volume", default=0) or 0
                    if prev_price * prev_vol < MIN_DOLLAR_VOL:
                        continue
                snapshots[ticker] = snap
            except Exception:
                continue

        universe = list(snapshots.keys())
        _log.info(f"[intraday_screen] Broad universe: {len(universe)} tickers passing ${MIN_DOLLAR_VOL:,} liquidity filter.")

        # Load 20d avg volumes from Supabase for known tickers
        avg_vol_map = _load_avg_vols(conn, universe)

        # Fall back to prevDay.v for tickers not in DB
        for ticker, snap in snapshots.items():
            if ticker not in avg_vol_map:
                try:
                    prev = _attr(snap, "prevDay", "prev_day")
                    prev_vol = _attr(prev, "v", "volume", default=0) or 0
                    if prev_vol > 0:
                        avg_vol_map[ticker] = float(prev_vol)
                except Exception:
                    pass

    else:
        # ── Default mode: quality universe + watchlist ─────────────────────────
        quality_tickers = list(quality_map.keys())
        combined = list(dict.fromkeys(quality_tickers + watchlist_tickers))  # deduped, order-preserving
        universe = combined

        avg_vol_map = _load_avg_vols(conn, universe)

        for i in range(0, len(universe), BATCH_SIZE):
            batch = universe[i : i + BATCH_SIZE]
            try:
                # tickers must be a direct kwarg — a params dict is ignored and
                # the call lazily fetches the FULL market (same bug fixed in the
                # news scanner). list() materializes so HTTP errors surface here.
                snaps = list(client.get_snapshot_all("stocks", tickers=",".join(batch)))
                for snap in snaps:
                    if hasattr(snap, "ticker") and snap.ticker:
                        snapshots[snap.ticker] = snap
            except Exception:
                pass

    if not universe:
        return []

    # Sector/name for any scanned ticker that isn't in the curated quality set.
    ticker_meta = _load_ticker_meta(conn, universe)

    # ── Score each ticker ─────────────────────────────────────────────────────
    results = []

    for ticker in universe:
        snap = snapshots.get(ticker)
        if snap is None:
            continue

        try:
            day = getattr(snap, "day", None)
            prev_day = _attr(snap, "prevDay", "prev_day")
            minute = getattr(snap, "min", None)

            today_open = _attr(day, "o", "open", default=0.0) or 0.0
            today_high = _attr(day, "h", "high", default=0.0) or 0.0
            today_low = _attr(day, "l", "low", default=0.0) or 0.0
            current_price = _attr(day, "c", "close", default=0.0) or 0.0
            today_volume = _attr(day, "v", "volume", default=0.0) or 0.0
            vwap = _attr(day, "vw", "vwap", default=0.0) or 0.0

            # Pre-market: no day bar yet — use the latest minute bar's price,
            # accumulated volume, and vwap so signals can still compute.
            if minute is not None:
                if not current_price:
                    current_price = _attr(minute, "c", "close", default=0.0) or 0.0
                if not today_volume:
                    today_volume = _attr(minute, "av", "accumulated_volume", default=0.0) or 0.0
                if not vwap:
                    vwap = _attr(minute, "vw", "vwap", default=0.0) or 0.0

            prev_close = _attr(prev_day, "c", "close", default=0.0) or 0.0

            # Derived metrics. Pre-market there's no official open — the gap
            # is the live (pre-market) price vs yesterday's close.
            if today_open > 0 and prev_close > 0:
                gap_pct = (today_open - prev_close) / prev_close * 100
            elif current_price > 0 and prev_close > 0 and not is_market_hours:
                gap_pct = (current_price - prev_close) / prev_close * 100
            else:
                gap_pct = 0.0
            change_pct = _attr(snap, "todaysChangePerc", "todays_change_percent")
            if change_pct is None:
                change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

            avg_vol = avg_vol_map.get(ticker, 0.0)

            # Volume pace ratio — accumulated volume vs what's NORMAL by this
            # time of day, using the data's age (snapshot lags DATA_DELAY_MIN).
            # Naive linear projection (vol/minutes*390) multiplies the opening
            # auction + pre-market volume by ~78x five minutes into the session
            # and flagged 700+ tickers at the 9:35 open. Expected fraction of
            # daily volume: ~12% by the open (pre-market + auction), then
            # roughly linear through the close.
            data_minutes = max(min(minutes_elapsed - DATA_DELAY_MIN, 390), 1)
            expected_frac = min(1.0, 0.12 + 0.88 * (data_minutes / 390.0))
            if avg_vol > 0 and today_volume > 0:
                vol_pace_ratio = today_volume / (avg_vol * expected_frac)
            else:
                vol_pace_ratio = 0.0

            above_vwap = (current_price > vwap) if vwap > 0 else False

            # Tradeability floor — skip names where today's traded dollars
            # couldn't absorb a real position (2K-share micro-caps were
            # flooding the scanner with untradeable "signals").
            if not single_ticker and current_price * today_volume < MIN_SIGNAL_DOLLAR_VOL:
                continue

            # Baseline sanity — gap/change math is only meaningful against a
            # real prior session. New listings / recycled symbols carry junk
            # prevDay data; a computed move past MAX_SANE_CHANGE_PCT is a
            # data artifact, not a setup.
            if not single_ticker:
                prev_volume = _attr(prev_day, "v", "volume", default=0.0) or 0.0
                if prev_close <= 0 or prev_close * prev_volume < MIN_PREV_DOLLAR_VOL:
                    continue
                if abs(change_pct) > MAX_SANE_CHANGE_PCT or abs(gap_pct) > MAX_SANE_CHANGE_PCT:
                    continue

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

            # Single-ticker lookups (the dashboard drawer) always return the
            # row — a stock between signals should show its live stats, not
            # "no data". The broad scan still drops NEUTRAL/low scores.
            if not single_ticker and (signal_type == "NEUTRAL" or score < min_score):
                continue
            if single_ticker and score < min_score:
                continue

            # Pull quality fundamentals if available; fall back to the broad
            # tickers table for name/sector so non-quality gappers aren't blank.
            q = quality_map.get(ticker, {})
            meta = ticker_meta.get(ticker, {})

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
                "company_name": q.get("company_name") or meta.get("company_name") or "",
                "sector": q.get("sector") or meta.get("sector") or "",
            }
            results.append(result)

        except Exception:
            continue

    # Label off-universe gappers (cached + capped live FMP /profile fetches).
    _enrich_missing_sectors(conn, results)

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
