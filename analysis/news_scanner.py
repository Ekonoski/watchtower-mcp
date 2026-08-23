"""
Watchtower — News scanner with Grok classification.

Fetches recent news from Polygon, classifies each article with Grok,
cross-references against live snapshot data (volume surge, price move),
and surfaces stocks you're NOT watching that have meaningful catalysts.

Designed to run alongside the intraday scan every 15-30 min.
Results fold into the intraday email as a second "News Alerts" section.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# High-signal news categories that warrant an alert
HIGH_SIGNAL_CATEGORIES = {
    "earnings_beat", "earnings_miss", "revenue_beat", "revenue_miss",
    "fda_approval", "fda_rejection", "clinical_trial",
    "merger", "acquisition", "buyout", "takeover",
    "analyst_initiation", "analyst_upgrade", "analyst_downgrade",
    "contract_win", "partnership", "product_launch",
    "guidance_raise", "guidance_cut",
    "insider_buying", "short_squeeze",
}

_GROK_CLASSIFY_SYSTEM = """You are a sharp equity news classifier for a professional trading system.
Given a news headline and summary, classify the article in JSON format.
Be concise, accurate, and focus on market-moving information only.
Return ONLY valid JSON, no other text."""

_GROK_CLASSIFY_TMPL = """Classify this news article for stock trading significance:

Ticker(s): {tickers}
Headline: {headline}
Summary: {summary}

Return JSON with these exact fields:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "magnitude": "high" | "medium" | "low",
  "category": one of: earnings_beat, earnings_miss, revenue_beat, revenue_miss, fda_approval, fda_rejection, clinical_trial, merger, acquisition, analyst_initiation, analyst_upgrade, analyst_downgrade, contract_win, partnership, product_launch, guidance_raise, guidance_cut, insider_buying, short_squeeze, general,
  "one_liner": "10 words max — what happened and why it matters"
}}"""

_GROK_SIGNAL_SYSTEM = """You are Eric Konoski's personal trading analyst on the Watchtower GMMSS system.
You are given a news catalyst AND the stock's current technical setup.
Your job is to synthesize both into an actionable trade signal — be direct, specific, and brutally honest.
If the setup is weak, say so. If it's high conviction, say so.
Return ONLY valid JSON, no other text."""

_GROK_SIGNAL_TMPL = """Synthesize this news catalyst with the current technical setup for ${ticker}:

NEWS CATALYST:
- Headline: {headline}
- Category: {category}
- Sentiment: {sentiment} | Magnitude: {magnitude}
- What happened: {one_liner}

TECHNICAL SETUP:
- Price: ${price:.2f} | Today's move: {change_pct:+.1f}%
- Volume: {vol_ratio:.1f}x normal
- RSI: {rsi}
- EMA 8/13: {ema_8} / {ema_13}
- 52w drawdown: {drawdown_pct:.0f}% off high
- Base breakout score: {s_base:.2f}/1.0
- RSI lift score: {s_rsi:.2f}/1.0
- Off-radar (not in standard universe): {is_off_radar}

Return JSON with these exact fields:
{{
  "combined_signal": "STRONG_BUY" | "BUY" | "WATCH" | "NEUTRAL" | "AVOID" | "STRONG_SELL" | "SELL",
  "conviction": "high" | "medium" | "low",
  "thesis": "2-3 sentences — why this specific catalyst + setup combination matters right now",
  "key_level": "the specific price level to watch for entry or confirmation (e.g. breakout above $X)",
  "risk": "the main risk to this thesis in one sentence"
}}"""


# Grok result caches keyed by article URL (2h TTL). With a 5-min scan cadence
# the same headline appears in many consecutive scans — reuse the classification
# and synthesis instead of re-billing Grok, while keeping the alert visible for
# the full lookback window.
_classification_cache: Dict[str, tuple] = {}  # url -> (ts, classification|None)
_synthesis_cache: Dict[str, tuple] = {}       # url -> (ts, signal dict)

# Catalysts stay surfaced for NEWS_RETENTION_MIN after first seen — a 7 AM
# FDA approval is still tradeable context at 10 AM. Without this, alerts
# vanish from the dashboard as soon as they age past the fetch lookback.
NEWS_RETENTION_SEC = int(os.environ.get("NEWS_RETENTION_MIN", "180")) * 60
_recent_alerts: Dict[str, tuple] = {}         # url -> (first_seen_ts, alert)

# How many catalysts the feed surfaces. The smarter ranking (price move) and the
# never-drop rule below do the real work, so the cap stays tight at 30 — a name
# already MOVING on its news is kept even past the cap (that's the whole point of
# the feed), so the cap no longer buries a breaking mover.
NEWS_MAX_ALERTS = int(os.environ.get("NEWS_MAX_ALERTS", "30"))
NEWS_BIG_MOVE_PCT = float(os.environ.get("NEWS_BIG_MOVE_PCT", "5.0"))


def _cache_prune(cache: Dict[str, tuple], ttl: float = 7200.0) -> None:
    import time as _time
    now = _time.time()
    for key, (ts, _) in list(cache.items()):
        if now - ts > ttl:
            del cache[key]


# ── Grok cache persistence ────────────────────────────────────────────────────
# The in-memory caches die with every deploy, and deploy-days re-bill the
# whole news backlog (~60 classifications x several deploys). Write-through
# to a small table and warm from it on the first scan of each process.

_GROK_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS grok_cache (
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key, kind)
);
"""

_cache_warmed = False
_pending_persist: List[tuple] = []  # (key, kind, payload-dict)


def _warm_caches_from_db() -> None:
    global _cache_warmed
    if _cache_warmed:
        return
    _cache_warmed = True
    import logging
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(_GROK_CACHE_SQL)
                cur.execute(
                    "SELECT key, kind, payload, extract(epoch from ts) "
                    "FROM grok_cache WHERE ts > now() - interval '2 hours'"
                )
                n = 0
                for key, kind, payload, ts in cur.fetchall():
                    if kind == "classification":
                        _classification_cache[key] = (float(ts), payload)
                    elif kind == "synthesis":
                        _synthesis_cache[key] = (float(ts), payload)
                    n += 1
            conn.commit()
            logging.getLogger(__name__).info(f"[news_scanner] Warmed {n} Grok cache entries from DB.")
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"[news_scanner] Cache warm failed (non-fatal): {e}")


def _flush_pending_persist() -> None:
    global _pending_persist
    if not _pending_persist:
        return
    batch, _pending_persist = _pending_persist, []
    import json as _json
    import logging
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(_GROK_CACHE_SQL)
                for key, kind, payload in batch:
                    cur.execute(
                        "INSERT INTO grok_cache (key, kind, payload) VALUES (%s, %s, %s::jsonb) "
                        "ON CONFLICT (key, kind) DO UPDATE SET payload = EXCLUDED.payload, ts = now()",
                        (key, kind, _json.dumps(payload)),
                    )
                cur.execute("DELETE FROM grok_cache WHERE ts < now() - interval '24 hours'")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"[news_scanner] Cache persist failed (non-fatal): {e}")


def _get_polygon_client():
    try:
        from analysis.polygon_data import get_client
        return get_client()
    except Exception:
        return None


def _get_grok_client():
    try:
        from analysis.grok_client import GrokClient
        return GrokClient()
    except Exception:
        return None


_FMP_BASE = "https://financialmodelingprep.com/stable"

# FMP meters the plan by data VOLUME on a rolling 30 days (2026-08-22:
# two usage warnings inside a week, 90% then 96%). The burn was here —
# every 5-minute scan refetched up to 1,000 articles from two feeds
# with no cache, ~250 heavyweight pulls a day of mostly-identical
# payloads. Two cuts, no trading behavior changed: a 250-article
# default (a 35-min window never fills it; NEWS_FMP_LIMIT restores the
# firehose) and a short TTL cache so back-to-back scans reuse the
# fetch instead of re-downloading the same headlines.
NEWS_FMP_LIMIT_DEFAULT = 250
NEWS_CACHE_TTL_S = int(os.environ.get("NEWS_CACHE_TTL_S", "150"))
_NEWS_CACHE: dict = {}   # lookback_minutes -> (fetched_at_utc, articles)


def _fetch_fmp_news(cutoff_utc: datetime) -> List[dict]:
    """
    Fetch latest stock news + press releases from FMP (market-wide feeds).
    Polygon's news feed is sparse (~6 articles/35 min pre-open); FMP Ultimate
    carries the firehose — earnings, 8-Ks, analyst notes, PRs.
    Returns articles normalized to the same shape as the Polygon fetch.
    """
    import logging
    log_ = logging.getLogger(__name__)

    api_key = os.environ.get("FMP_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        import pytz
        et = pytz.timezone("America/New_York")
    except ImportError:
        et = None

    import requests

    articles: List[dict] = []
    for endpoint in ("/news/stock-latest", "/news/press-releases-latest"):
        try:
            resp = requests.get(
                f"{_FMP_BASE}{endpoint}",
                params={"page": 0,
                        "limit": int(os.environ.get(
                            "NEWS_FMP_LIMIT", str(NEWS_FMP_LIMIT_DEFAULT))),
                        "apikey": api_key},
                timeout=20,
            )
            resp.raise_for_status()
            items = resp.json() or []
        except Exception as e:
            log_.warning(f"[news_scanner] FMP {endpoint} fetch error: {e}")
            continue

        for item in items:
            try:
                symbol = (item.get("symbol") or "").upper().strip()
                if not symbol or len(symbol) > 5 or not symbol.replace(".", "").isalnum():
                    continue
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                # FMP publishedDate is US/Eastern naive ("2026-06-10 09:05:00")
                pub_raw = item.get("publishedDate") or ""
                try:
                    pub_naive = datetime.strptime(pub_raw, "%Y-%m-%d %H:%M:%S")
                    pub_utc = (et.localize(pub_naive).astimezone(timezone.utc)
                               if et else pub_naive.replace(tzinfo=timezone.utc))
                except ValueError:
                    continue
                if pub_utc < cutoff_utc:
                    continue
                articles.append({
                    "tickers": [symbol],
                    "headline": title,
                    "summary": (item.get("text") or "")[:400],
                    "published_utc": pub_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "article_url": item.get("url") or "",
                    "publisher": item.get("publisher") or item.get("site") or "FMP",
                })
            except Exception:
                continue

    return articles


def fetch_recent_news(lookback_minutes: int = 35) -> List[dict]:
    """
    Fetch news articles published in the last N minutes from Polygon + FMP.
    Returns list of raw article dicts with tickers, headline, description, published_utc.
    """
    now = datetime.now(timezone.utc)
    cached = _NEWS_CACHE.get(lookback_minutes)
    if cached and (now - cached[0]).total_seconds() < NEWS_CACHE_TTL_S:
        return list(cached[1])

    client = _get_polygon_client()
    if not client:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    articles = []
    raw_count = 0
    no_ticker = 0
    try:
        news = client.list_ticker_news(
            published_utc_gte=cutoff_str,
            order="desc",
            limit=200,
            sort="published_utc",
        )
        count = 0
        for article in news:
            count += 1
            if count > 200:  # list_ticker_news paginates past `limit` lazily
                break
            raw_count += 1
            tickers = getattr(article, "tickers", []) or []
            if not tickers:
                no_ticker += 1
                continue
            articles.append({
                "tickers": [t.upper() for t in tickers if len(t) <= 5],
                "headline": getattr(article, "title", "") or "",
                "summary": (getattr(article, "description", "") or "")[:400],
                "published_utc": getattr(article, "published_utc", "") or "",
                "article_url": getattr(article, "article_url", "") or "",
                "publisher": getattr(getattr(article, "publisher", None), "name", "") or "",
            })
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[news_scanner] Polygon news fetch error: {e}")

    polygon_kept = len(articles)

    # Second source: FMP stock news + press releases. Dedupe on normalized
    # headline (the same PR is syndicated across both feeds).
    fmp_articles = _fetch_fmp_news(cutoff)
    seen_headlines = {a["headline"].lower()[:80] for a in articles}
    seen_urls = {a["article_url"] for a in articles if a.get("article_url")}
    fmp_added = 0
    for a in fmp_articles:
        hl_key = a["headline"].lower()[:80]
        if hl_key in seen_headlines or (a["article_url"] and a["article_url"] in seen_urls):
            continue
        seen_headlines.add(hl_key)
        if a["article_url"]:
            seen_urls.add(a["article_url"])
        articles.append(a)
        fmp_added += 1

    import logging
    logging.getLogger(__name__).info(
        f"[news_scanner] Fetch since {cutoff_str}: polygon {raw_count} raw "
        f"({no_ticker} no-ticker, {polygon_kept} kept) + fmp {len(fmp_articles)} "
        f"({fmp_added} new) = {len(articles)} articles."
    )
    _NEWS_CACHE[lookback_minutes] = (now, list(articles))
    return articles


def classify_article(article: dict, grok) -> Optional[dict]:
    """
    Use Grok to classify a single news article.
    Returns classification dict or None on failure.
    """
    headline = article.get("headline", "").strip()
    if not headline:
        return None

    tickers_str = ", ".join(article.get("tickers", []))
    summary = article.get("summary", "")

    try:
        resp = grok.chat(
            system=_GROK_CLASSIFY_SYSTEM,
            user=_GROK_CLASSIFY_TMPL.format(
                tickers=tickers_str,
                headline=headline,
                summary=summary,
            ),
            json_mode=True,
            temperature=0.2,
            max_tokens=200,
        )
        parsed = resp.get("parsed")
        if not parsed:
            return None

        return {
            "sentiment": parsed.get("sentiment", "neutral"),
            "magnitude": parsed.get("magnitude", "low"),
            "category": parsed.get("category", "general"),
            "one_liner": parsed.get("one_liner", headline[:80]),
        }
    except Exception:
        return None


def synthesize_with_technicals(alert: dict, technical_data: dict, grok) -> Optional[dict]:
    """
    Second Grok pass: cross-reference news catalyst with technical setup.
    Only called for medium/high magnitude alerts that also have technical data.

    Returns a signal dict with combined_signal, conviction, thesis, key_level, risk.
    """
    try:
        resp = grok.chat(
            system=_GROK_SIGNAL_SYSTEM,
            user=_GROK_SIGNAL_TMPL.format(
                ticker=alert["primary_ticker"],
                headline=alert.get("headline", ""),
                category=alert.get("category", ""),
                sentiment=alert.get("sentiment", ""),
                magnitude=alert.get("magnitude", ""),
                one_liner=alert.get("one_liner", ""),
                price=technical_data.get("price", 0),
                change_pct=alert.get("change_pct", 0),
                vol_ratio=alert.get("vol_ratio", 1.0),
                rsi=technical_data.get("rsi") or "N/A",
                ema_8=f"${technical_data['ema_8']:.2f}" if technical_data.get("ema_8") else "N/A",
                ema_13=f"${technical_data['ema_13']:.2f}" if technical_data.get("ema_13") else "N/A",
                drawdown_pct=technical_data.get("drawdown_pct", 0),
                s_base=technical_data.get("_s_base", 0),
                s_rsi=technical_data.get("_s_rsi", 0),
                is_off_radar=alert.get("is_off_radar", False),
            ),
            json_mode=True,
            temperature=0.3,
            max_tokens=400,
        )
        parsed = resp.get("parsed")
        if not parsed:
            return None

        return {
            "combined_signal": parsed.get("combined_signal", "WATCH"),
            "conviction": parsed.get("conviction", "low"),
            "thesis": parsed.get("thesis", ""),
            "key_level": parsed.get("key_level", ""),
            "risk": parsed.get("risk", ""),
        }
    except Exception:
        return None


def _fetch_technicals_for_alert(ticker: str) -> dict:
    """
    Fetch recent bars from Polygon and compute technical indicators
    needed for the Grok synthesis prompt.
    """
    try:
        from analysis.polygon_data import fetch_recent_bars, compute_basic_technicals
        bars = fetch_recent_bars(ticker, days=120)
        if not bars or len(bars) < 30:
            return {}

        tech = compute_basic_technicals(bars)

        # Add RSI lift and base scores using upcomer screen logic
        try:
            import pandas as pd
            import numpy as np
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from screen.reversal_screen import compute_rsi, compute_ema
            from screen.upcomer_screen import score_base_breakout, score_rsi_lift, _detect_base

            closes = pd.Series([b["close"] for b in bars], dtype=float)
            volumes = pd.Series([b["volume"] for b in bars], dtype=float)

            rsi_series = compute_rsi(closes)
            ema_8 = compute_ema(closes, 8)
            ema_13 = compute_ema(closes, 13)

            tech["rsi"] = round(rsi_series.iloc[-1], 1) if len(rsi_series) > 0 else None
            tech["ema_8"] = round(ema_8.iloc[-1], 2) if len(ema_8) > 0 else None
            tech["ema_13"] = round(ema_13.iloc[-1], 2) if len(ema_13) > 0 else None
            tech["_s_base"] = score_base_breakout(closes, volumes)
            tech["_s_rsi"] = score_rsi_lift(rsi_series)

            hi_52w = closes.iloc[-252:].max() if len(closes) >= 252 else closes.max()
            last = closes.iloc[-1]
            tech["drawdown_pct"] = round((hi_52w - last) / hi_52w * 100, 1) if hi_52w > 0 else 0
        except Exception:
            pass

        return tech
    except Exception:
        return {}


def _fetch_snapshot_map(tickers: List[str]) -> Dict[str, dict]:
    """Fetch Polygon snapshots for a list of tickers to get live price/volume."""
    client = _get_polygon_client()
    if not client or not tickers:
        return {}

    import logging as _log
    _logger = _log.getLogger(__name__)
    out = {}
    try:
        # Pass tickers as a direct kwarg — Polygon client forwards extra kwargs as
        # query params. Wrapping in list() forces the lazy iterator to execute now
        # so any HTTP error is caught here rather than silently swallowed mid-loop.
        snaps = list(client.get_snapshot_all("stocks", tickers=",".join(tickers)))
        _logger.info(f"[news_scanner] Snapshot returned {len(snaps)} results for {len(tickers)} tickers.")
    except Exception as e:
        _logger.warning(f"[news_scanner] Snapshot fetch failed: {e}")
        return out

    def _attr(obj, *names, default=None):
        # Polygon REST JSON is camelCase; the Python client's dataclasses are
        # snake_case — read both so neither shape silently returns None.
        if obj is None:
            return default
        for n in names:
            v = getattr(obj, n, None)
            if v is not None:
                return v
        return default

    for s in snaps:
        try:
            ticker = getattr(s, "ticker", None)
            if not ticker:
                continue
            # Pre-market the delayed feed has no `day` bar — fall back to the
            # latest minute bar / last trade so news cards show the real
            # pre-market price and % move instead of $0.00 / +0.0%.
            day = getattr(s, "day", None)
            prev_day = _attr(s, "prevDay", "prev_day")
            minute = getattr(s, "min", None)
            last_trade = _attr(s, "lastTrade", "last_trade")

            price = _attr(day, "c", "close", default=0) or 0
            if not price:
                price = _attr(minute, "c", "close", default=0) or 0
            if not price:
                price = _attr(last_trade, "p", "price", default=0) or 0

            volume = _attr(day, "v", "volume", default=0) or 0
            if not volume:
                volume = _attr(minute, "av", "accumulated_volume", default=0) or 0

            prev_close = _attr(prev_day, "c", "close")
            if not price and prev_close:
                price = prev_close
            change_pct = ((price - prev_close) / prev_close * 100) if (prev_close and prev_close > 0 and price) else 0
            prev_vol = _attr(prev_day, "v", "volume")
            # 0 = "no volume data yet" — display layer shows it honestly
            # instead of a fake 1.0x.
            vol_ratio = (volume / prev_vol) if (volume and prev_vol and prev_vol > 0) else 0

            out[ticker] = {
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
                "vol_ratio": vol_ratio,
            }
        except Exception:
            continue
    return out


def _load_known_tickers(conn) -> set:
    """Load tickers already in quality_universe + watchlist — these are 'known'."""
    known = set()
    if not conn:
        return known
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM quality_universe")
            known.update(r[0] for r in cur.fetchall())
    except Exception:
        pass
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM watchlist WHERE active = TRUE")
            known.update(r[0] for r in cur.fetchall())
    except Exception:
        pass
    return known


def run_news_scan(lookback_minutes: int = 35) -> List[dict]:
    """
    Run the full news scan pipeline:
      1. Fetch recent Polygon news
      2. Classify each article with Grok
      3. Filter to high-signal, market-moving news only
      4. Enrich with live snapshot data (price change, volume surge)
      5. Flag tickers not in your current universe (off-radar discoveries)
      6. Return ranked list of news alerts

    Returns list of alert dicts ready for email formatting.
    """
    conn = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from screen.reversal_screen import _conn
        conn = _conn()
    except Exception:
        pass

    # conn's only job is this one lookup — release it immediately (this scan
    # runs every 5 min; holding an idle conn for the multi-minute Grok
    # classification loop below starves the Supabase pool).
    known_tickers = _load_known_tickers(conn)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    import logging as _logging
    _nlog = _logging.getLogger(__name__)

    # Step 1: fetch news
    articles = fetch_recent_news(lookback_minutes=lookback_minutes)
    if not articles and not _recent_alerts:
        return []

    _warm_caches_from_db()
    _cache_prune(_classification_cache)
    _cache_prune(_synthesis_cache)

    # Deduplicate — same ticker can appear in multiple articles
    seen_tickers: set = set()
    grok = _get_grok_client()

    # Bound Grok spend/scan time when the feeds are busy: classify at most
    # N uncached articles per scan with Grok, keyword-classify the overflow.
    max_classify = int(os.environ.get("NEWS_MAX_CLASSIFY", "60"))
    grok_classified = 0

    alerts = []
    for article in articles:
        tickers = article.get("tickers", [])
        # Only process articles with 1-3 tickers (broad market news = low signal)
        if not tickers or len(tickers) > 3:
            continue

        # Skip if we've already processed these tickers this scan
        primary_ticker = tickers[0]
        if primary_ticker in seen_tickers:
            continue

        # Classify with Grok if available, else use basic keyword filter.
        # Cached results are reused across scans (same article, same answer).
        cache_key = article.get("article_url") or article.get("headline", "")
        cached = _classification_cache.get(cache_key)
        if cached is not None:
            classification = cached[1]
        elif grok and grok_classified < max_classify:
            classification = classify_article(article, grok)
            grok_classified += 1
            time.sleep(0.1)  # rate limit Grok calls
            if classification is not None and cache_key:
                _classification_cache[cache_key] = (time.time(), classification)
                _pending_persist.append((cache_key, "classification", classification))
        else:
            classification = _keyword_classify(article)
            if cache_key:
                _classification_cache[cache_key] = (time.time(), classification)

        if not classification:
            continue

        magnitude = classification.get("magnitude", "low")
        category = classification.get("category", "general")
        sentiment = classification.get("sentiment", "neutral")

        # Filter: only medium/high magnitude, non-general
        if magnitude == "low" and category == "general":
            continue
        if category == "general" and sentiment == "neutral":
            continue

        seen_tickers.update(tickers)
        alerts.append({
            "tickers": tickers,
            "primary_ticker": primary_ticker,
            "headline": article["headline"],
            "article_url": article.get("article_url", ""),
            "publisher": article.get("publisher", ""),
            "published_utc": article.get("published_utc", ""),
            "sentiment": sentiment,
            "magnitude": magnitude,
            "category": category,
            "one_liner": classification.get("one_liner", article["headline"][:80]),
            "is_off_radar": primary_ticker not in known_tickers,
        })

    _nlog.info(
        f"[news_scanner] Classified: {len(alerts)} alerts from {len(articles)} articles "
        f"(grok={'yes' if grok else 'no'}), {len(_recent_alerts)} retained."
    )

    # Merge with recently surfaced catalysts so they stay visible for
    # NEWS_RETENTION_MIN even after sliding out of the fetch lookback.
    # Retained alerts get re-enriched with live prices below like new ones.
    now_ts = time.time()
    for key, (ts, _old) in list(_recent_alerts.items()):
        if now_ts - ts > NEWS_RETENTION_SEC:
            del _recent_alerts[key]
    current_keys = set()
    for a in alerts:
        key = a.get("article_url") or a.get("headline", "")
        if key:
            current_keys.add(key)
            first_seen = _recent_alerts.get(key, (now_ts, None))[0]
            _recent_alerts[key] = (first_seen, a)
    for key, (_ts, old) in _recent_alerts.items():
        if key not in current_keys:
            alerts.append(old)

    if not alerts:
        _flush_pending_persist()
        return []

    # Step 4: enrich with live snapshot data
    all_tickers = list({a["primary_ticker"] for a in alerts})
    snap_map = _fetch_snapshot_map(all_tickers)

    for alert in alerts:
        ticker = alert["primary_ticker"]
        snap = snap_map.get(ticker, {})
        alert["price"] = snap.get("price", 0)
        alert["change_pct"] = snap.get("change_pct", 0)
        alert["vol_ratio"] = snap.get("vol_ratio", 0)

    # Step 5: Grok synthesis — cross-reference news with technicals
    # Only for medium/high magnitude alerts — cap at 8 to manage API calls
    if grok:
        synthesis_candidates = [
            a for a in alerts
            if a.get("magnitude") in ("high", "medium")
        ][:8]

        for alert in synthesis_candidates:
            try:
                cache_key = alert.get("article_url") or alert.get("headline", "")
                cached = _synthesis_cache.get(cache_key)
                if cached is not None:
                    signal = cached[1]
                else:
                    tech = _fetch_technicals_for_alert(alert["primary_ticker"])
                    signal = synthesize_with_technicals(alert, tech, grok) if tech else None
                    if signal and cache_key:
                        _synthesis_cache[cache_key] = (time.time(), signal)
                        _pending_persist.append((cache_key, "synthesis", signal))
                    time.sleep(0.15)
                if signal:
                    alert["combined_signal"] = signal.get("combined_signal", "WATCH")
                    alert["conviction"] = signal.get("conviction", "low")
                    alert["thesis"] = signal.get("thesis", "")
                    alert["key_level"] = signal.get("key_level", "")
                    alert["risk"] = signal.get("risk", "")
                    alert["has_synthesis"] = True
            except Exception:
                pass

    # Step 6: rank — combined signal first, then magnitude, then off-radar, then vol
    _signal_rank = {
        "STRONG_BUY": 6, "STRONG_SELL": 6,
        "BUY": 5, "SELL": 5,
        "WATCH": 4,
        "NEUTRAL": 2,
        "AVOID": 1,
    }

    def _rank_key(a):
        sig_score = _signal_rank.get(a.get("combined_signal", ""), 0)
        mag_score = {"high": 3, "medium": 2, "low": 1}.get(a["magnitude"], 0)
        off_radar_bonus = 1 if a["is_off_radar"] else 0
        vol_bonus = min(2, a.get("vol_ratio", 1.0) - 1.0)
        # The price reaction IS the catalyst — a name already moving hard on its
        # news is the highest-signal item in the feed, so let it float to the top
        # regardless of headline classification or whether we already follow it.
        move_bonus = min(5.0, abs(a.get("change_pct", 0) or 0) / 2.0)
        return sig_score + mag_score + off_radar_bonus + vol_bonus + move_bonus

    alerts.sort(key=_rank_key, reverse=True)
    _flush_pending_persist()

    # Keep the top N, but NEVER cut a name that's already making a real move on
    # its news (>= NEWS_BIG_MOVE_PCT) — those are exactly the breaking catalysts
    # the feed exists to surface, even on a busy pre-market when the cap binds.
    top = alerts[:NEWS_MAX_ALERTS]
    if len(alerts) > NEWS_MAX_ALERTS:
        kept = set(map(id, top))
        for a in alerts[NEWS_MAX_ALERTS:]:
            if abs(a.get("change_pct", 0) or 0) >= NEWS_BIG_MOVE_PCT and id(a) not in kept:
                top.append(a)
    return top


def _keyword_classify(article: dict) -> Optional[dict]:
    """
    Fallback classifier when Grok is unavailable.
    Simple keyword matching on headline.
    """
    headline = (article.get("headline", "") + " " + article.get("summary", "")).lower()

    category = "general"
    sentiment = "neutral"
    magnitude = "low"

    keyword_map = [
        (["beats", "beat estimates", "tops estimates", "record revenue", "record earnings"], "earnings_beat", "bullish", "high"),
        (["misses", "miss estimates", "below estimates", "disappoints"], "earnings_miss", "bearish", "high"),
        (["fda approves", "fda approval", "approved by fda"], "fda_approval", "bullish", "high"),
        (["fda rejects", "fda rejection", "complete response letter", "crl"], "fda_rejection", "bearish", "high"),
        (["merger", "acquisition", "acquires", "buyout", "takeover bid"], "merger", "bullish", "high"),
        (["initiates", "initiation", "initiating coverage", "starts coverage"], "analyst_initiation", "bullish", "medium"),
        (["upgrades", "upgraded to buy", "upgraded to outperform"], "analyst_upgrade", "bullish", "medium"),
        (["downgrades", "downgraded to sell", "downgraded to underperform"], "analyst_downgrade", "bearish", "medium"),
        (["raises guidance", "raises outlook", "raises forecast"], "guidance_raise", "bullish", "medium"),
        (["cuts guidance", "lowers guidance", "lowers outlook", "lowers forecast"], "guidance_cut", "bearish", "medium"),
        (["wins contract", "awarded contract", "partnership", "collaboration"], "contract_win", "bullish", "medium"),
    ]

    for keywords, cat, sent, mag in keyword_map:
        if any(kw in headline for kw in keywords):
            category = cat
            sentiment = sent
            magnitude = mag
            break

    if category == "general":
        return None  # no signal worth alerting on

    return {
        "sentiment": sentiment,
        "magnitude": magnitude,
        "category": category,
        "one_liner": article["headline"][:80],
    }
