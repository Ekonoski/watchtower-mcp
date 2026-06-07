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

_GROK_SYSTEM = """You are a sharp equity news classifier for a professional trading system.
Given a news headline and summary, classify the article in JSON format.
Be concise, accurate, and focus on market-moving information only.
Return ONLY valid JSON, no other text."""

_GROK_USER_TMPL = """Classify this news article for stock trading significance:

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


def fetch_recent_news(lookback_minutes: int = 35) -> List[dict]:
    """
    Fetch news articles from Polygon published in the last N minutes.
    Returns list of raw article dicts with tickers, headline, description, published_utc.
    """
    client = _get_polygon_client()
    if not client:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    articles = []
    try:
        news = client.list_ticker_news(
            published_utc_gte=cutoff_str,
            order="desc",
            limit=50,
            sort="published_utc",
        )
        for article in news:
            tickers = getattr(article, "tickers", []) or []
            if not tickers:
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
        print(f"[news_scanner] Polygon news fetch error: {e}", file=sys.stderr)

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
            system=_GROK_SYSTEM,
            user=_GROK_USER_TMPL.format(
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

        sentiment = parsed.get("sentiment", "neutral")
        magnitude = parsed.get("magnitude", "low")
        category = parsed.get("category", "general")
        one_liner = parsed.get("one_liner", headline[:80])

        return {
            "sentiment": sentiment,
            "magnitude": magnitude,
            "category": category,
            "one_liner": one_liner,
        }
    except Exception:
        return None


def _fetch_snapshot_map(tickers: List[str]) -> Dict[str, dict]:
    """Fetch Polygon snapshots for a list of tickers to get live price/volume."""
    client = _get_polygon_client()
    if not client or not tickers:
        return {}

    out = {}
    try:
        snaps = client.get_snapshot_all("stocks", ticker_any_of=",".join(tickers))
        for s in snaps:
            ticker = getattr(s, "ticker", None)
            if not ticker:
                continue
            day = getattr(s, "day", None)
            prev_day = getattr(s, "prev_day", None)
            if not day:
                continue

            price = getattr(day, "c", None) or 0
            volume = getattr(day, "v", None) or 0
            prev_close = getattr(prev_day, "c", None) if prev_day else None
            change_pct = ((price - prev_close) / prev_close * 100) if (prev_close and prev_close > 0) else 0

            # Volume vs previous day
            prev_vol = getattr(prev_day, "v", None) if prev_day else None
            vol_ratio = (volume / prev_vol) if (prev_vol and prev_vol > 0) else 1.0

            out[ticker] = {
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
                "vol_ratio": vol_ratio,
            }
    except Exception:
        pass
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

    known_tickers = _load_known_tickers(conn)

    # Step 1: fetch news
    articles = fetch_recent_news(lookback_minutes=lookback_minutes)
    if not articles:
        return []

    # Deduplicate — same ticker can appear in multiple articles
    seen_tickers: set = set()
    grok = _get_grok_client()

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

        # Classify with Grok if available, else use basic keyword filter
        if grok:
            classification = classify_article(article, grok)
            time.sleep(0.1)  # rate limit Grok calls
        else:
            classification = _keyword_classify(article)

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
            "publisher": article.get("publisher", ""),
            "published_utc": article.get("published_utc", ""),
            "sentiment": sentiment,
            "magnitude": magnitude,
            "category": category,
            "one_liner": classification.get("one_liner", article["headline"][:80]),
            "is_off_radar": primary_ticker not in known_tickers,
        })

    if not alerts:
        return []

    # Step 4: enrich with live snapshot data
    all_tickers = list({a["primary_ticker"] for a in alerts})
    snap_map = _fetch_snapshot_map(all_tickers)

    for alert in alerts:
        ticker = alert["primary_ticker"]
        snap = snap_map.get(ticker, {})
        alert["price"] = snap.get("price", 0)
        alert["change_pct"] = snap.get("change_pct", 0)
        alert["vol_ratio"] = snap.get("vol_ratio", 1.0)

    # Step 5: rank — high magnitude first, then off-radar, then by vol surge
    def _rank_key(a):
        mag_score = {"high": 3, "medium": 2, "low": 1}.get(a["magnitude"], 0)
        off_radar_bonus = 1 if a["is_off_radar"] else 0
        vol_bonus = min(2, a.get("vol_ratio", 1.0) - 1.0)
        return mag_score + off_radar_bonus + vol_bonus

    alerts.sort(key=_rank_key, reverse=True)
    return alerts[:20]  # cap at 20 per scan


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
