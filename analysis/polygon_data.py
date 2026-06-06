"""
Polygon.io (Massive.com) data helper for Watchtower GMMSS.

Provides clean, high-fidelity market data (bars/aggregates, snapshots, options, volume)
as the preferred source for technicals, volume surge, regime, RS, and especially
bearish/put sleeve (options snapshots for concrete defined-risk ideas).

Now the primary live data source for screens when key is present (DB remains source
of truth for historical backtests and fundamentals).

Usage:
    from analysis.polygon_data import get_client, fetch_recent_bars, enrich_with_polygon, fetch_options_snapshot

Requires POLYGON_API_KEY in .env (keys from polygon.io continue to work).

Free tier works for light testing. Starter or Developer recommended for daily + options.
"""

import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
import pandas as pd

try:
    from polygon import RESTClient
    from polygon.rest.models import Agg
except ImportError:
    RESTClient = None

def get_client() -> Optional["RESTClient"]:
    key = os.environ.get("POLYGON_API_KEY")
    if not key or RESTClient is None:
        return None
    return RESTClient(api_key=key)

def fetch_recent_bars(ticker: str, days: int = 300, multiplier: int = 1, timespan: str = "day") -> List[Dict[str, Any]]:
    """
    Fetch daily (or other) bars for ticker.
    Returns list of dicts with date, open, high, low, close, volume, etc.
    """
    client = get_client()
    if not client:
        return []
    try:
        end = date.today()
        start = end - timedelta(days=days + 30)  # buffer
        aggs = list(client.get_aggs(
            ticker,
            multiplier=multiplier,
            timespan=timespan,
            from_=start.isoformat(),
            to=end.isoformat(),
            limit=50000,
        ))
        bars = []
        for a in aggs:
            bars.append({
                "date": date.fromtimestamp(a.timestamp / 1000).isoformat(),
                "open": a.open,
                "high": a.high,
                "low": a.low,
                "close": a.close,
                "volume": a.volume,
                "vwap": getattr(a, "vwap", None),
            })
        return bars
    except Exception as e:
        print(f"[polygon] Error fetching bars for {ticker}: {e}")
        return []

def compute_basic_technicals(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute simple technicals from bars list (latest first or sorted asc)."""
    if not bars or len(bars) < 50:
        return {}
    # Assume sorted oldest to newest
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    latest_close = closes[-1]
    latest_vol = volumes[-1]

    # EMAs (simple approx)
    def ema(data, period):
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema_val = data[0]
        for price in data[1:]:
            ema_val = (price * multiplier) + (ema_val * (1 - multiplier))
        return ema_val

    ema8 = ema(closes, 8)
    ema13 = ema(closes, 13)
    ema50 = ema(closes, 50)

    # Volume surge
    avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else latest_vol
    vol_surge = latest_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

    # Simple RSI (14)
    def rsi(closes, period=14):
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i-1]
            if delta > 0:
                gains.append(delta)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(delta))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    rsi14 = rsi(closes)

    # % off high (52w approx, last ~250 bars)
    high_250 = max(b["high"] for b in bars[-250:]) if len(bars) >= 250 else max(b["high"] for b in bars)
    pct_off_high = (high_250 - latest_close) / high_250 * 100 if high_250 > latest_close else 0

    return {
        "current_price": latest_close,
        "ema_8": round(ema8, 2) if ema8 else None,
        "ema_13": round(ema13, 2) if ema13 else None,
        "ema_50": round(ema50, 2) if ema50 else None,
        "rsi_14": round(rsi14, 1) if rsi14 else None,
        "volume_surge_20d": round(vol_surge, 2),
        "pct_off_52w_high": round(pct_off_high, 1),
        "latest_volume": latest_vol,
        "avg_volume_20d": round(avg_vol_20),
    }


def enrich_with_polygon(ticker: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich a context dict with technicals (Polygon preferred, yfinance fallback) and market regime.
    Now the preferred path for GMMSS live runs when POLYGON_API_KEY is set.
    """
    client = get_client()
    bars = []
    source = "yfinance_fallback"
    if client:
        bars = fetch_recent_bars(ticker, days=300)
        if bars:
            source = "polygon"
    if not bars:
        # Fallback to yfinance for technicals - robust for current yf versions
        try:
            import yfinance as yf
            hist = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
            if not hist.empty:
                # Robust extraction
                close_ser = hist["Close"] if "Close" in hist.columns else hist.iloc[:, 0]
                closes = list(close_ser.astype(float))
                vol_ser = hist["Volume"] if "Volume" in hist.columns else pd.Series([0] * len(closes))
                volumes = list(vol_ser.astype(int))
                high_ser = hist["High"] if "High" in hist.columns else close_ser
                highs = list(high_ser.astype(float))
                dates = [str(d.date()) for d in hist.index]
                bars = [{"date": d, "close": c, "volume": v, "high": h} for d, c, v, h, in zip(dates, closes, volumes, highs)]
                if bars:
                    source = "yfinance"
        except Exception as e:
            ctx["yf_fallback_error"] = str(e)[:120]
    if bars:
        try:
            tech = compute_basic_technicals(bars)
            ctx.update(tech)
            ctx["data_source_technicals"] = source
        except Exception as e:
            ctx["compute_error"] = str(e)[:120]
            ctx["data_source_technicals"] = source  # still note source
    else:
        ctx["polygon_note"] = "No data source available for technicals"

    # Always try regime via yf (lightweight) - can be upgraded to Polygon SPY bars later
    try:
        import yfinance as yf
        spy = yf.download("SPY", period="1y", progress=False, auto_adjust=True)
        if not spy.empty and len(spy) >= 200:
            close_ser = spy["Close"].squeeze().astype(float) if isinstance(spy, pd.DataFrame) and "Close" in spy.columns else pd.Series(spy.iloc[:, 0]).astype(float)
            closes = close_ser.tolist()
            ma200 = float(close_ser.tail(200).mean())
            latest_spy = float(closes[-1])
            ctx["market_spy_above_200ma"] = latest_spy > ma200
            ctx["market_spy_price"] = round(latest_spy, 2)
            ctx["market_spy_200ma"] = round(ma200, 2)
    except Exception as e:
        ctx["regime_error"] = str(e)[:80]

    return ctx


def fetch_options_snapshot(underlying: str, num_contracts: int = 6) -> dict:
    """Fetch options snapshot data focused on puts for the bearish/put sleeve (GMMSS Sleeve 3).
    Returns near-term put info, underlying price, and suggested strikes for defined-risk ideas.
    Requires Polygon options access (Developer tier or add-on recommended).
    Falls back gracefully.
    """
    client = get_client()
    if not client:
        return {"error": "no_polygon_client"}

    result = {"underlying": underlying.upper(), "source": "polygon", "puts": []}
    try:
        # Get current stock snapshot for underlying price
        try:
            stock_snap = client.get_snapshot("stocks", underlying)
            if hasattr(stock_snap, 'day') and stock_snap.day:
                result["underlying_price"] = round(stock_snap.day.c, 2)
            elif hasattr(stock_snap, 'lastTrade'):
                result["underlying_price"] = round(stock_snap.lastTrade.price, 2)
        except Exception:
            pass

        # Try to get options snapshots for puts (near term, around the money)
        # Use get_snapshot_all for options with params for underlying (avoids kwarg issues on some client versions)
        puts_found = []
        try:
            # params dict for compatibility; contract_type=put for bearish sleeve focus
            snapshots = client.get_snapshot_all(
                "options",
                params={
                    "contract_type": "put",
                    "underlying_ticker": underlying,
                    "expiration_date.gte": (date.today() + timedelta(days=7)).isoformat()
                },
                limit=50
            )
            for s in list(snapshots)[:num_contracts]:
                det = getattr(s, 'details', None)
                if not det:
                    continue
                strike = getattr(det, 'strike_price', None)
                exp = getattr(det, 'expiration_date', None)
                last = getattr(s, 'last_trade', None) or getattr(s, 'day', None)
                price = getattr(last, 'c', None) or getattr(last, 'price', None) if last else None
                if strike and price:
                    puts_found.append({
                        "ticker": getattr(det, 'ticker', f"{underlying}{exp}P{int(strike*1000):08d}"),
                        "strike": strike,
                        "expiration": str(exp) if exp else None,
                        "last_price": round(price, 2) if price else None,
                        "volume": getattr(getattr(s, 'day', None), 'v', None),
                    })
        except Exception as e:
            result["options_fetch_note"] = f"options snapshot limited or requires higher tier: {str(e)[:80]}"

        result["puts"] = puts_found[:num_contracts]
        if not puts_found:
            result["note"] = "No put contracts returned (check tier or try specific option tickers). Polygon options access recommended for full Sleeve 3 power."
        return result
    except Exception as e:
        return {"error": str(e)[:120], "underlying": underlying}


def compute_live_technicals_from_polygon(ticker: str, days: int = 60) -> dict:
    """Fetch fresh bars from Polygon and compute the core live indicators used by screens
    (vol_surge, regime via SPY, basic price/volume stats). Returns dict ready to merge into row.
    Falls back to empty if no key or error. Preferred for live daily runs in GMMSS.
    """
    client = get_client()
    if not client:
        return {}
    try:
        bars = fetch_recent_bars(ticker, days=days)
        if not bars or len(bars) < 10:
            return {}
        # Reuse the basic compute
        tech = compute_basic_technicals(bars)
        # Add a fresh volume surge using same spirit as reversal_screen
        vols = [b["volume"] for b in bars if b.get("volume")]
        if len(vols) >= 20:
            recent = sum(vols[-20:]) / 20
            overall = sum(vols) / len(vols)
            tech["live_vol_surge"] = round(recent / overall, 2) if overall > 0 else 1.0
        tech["live_data_source"] = "polygon"
        return tech
    except Exception:
        return {}
