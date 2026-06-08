"""
Watchtower — Social buzz / X sentiment via Grok API.

Two jobs:
  1. query_ticker_sentiment(ticker) — ask Grok what X is saying about a stock
     right now. Returns sentiment, score, and a 1-sentence summary.
     Used on-demand in watchtower_analyze_ticker.

  2. run_social_buzz_scan(tickers) — batch sentiment scan for a list of tickers.
     Writes results back to Supabase social_buzz table with sentiment + rank_surge.
     Scheduled daily (runs after market close).

Grok has live X access — this is the one signal layer that's genuinely
real-time even on our delayed data setup.
"""

import os
import sys
import time
from datetime import date
from typing import Dict, List, Optional

_SENTIMENT_SYSTEM = """You are a social sentiment analyst for a professional trading system.
You have access to real-time X (Twitter) data. Analyze what traders and investors
are currently saying about the given stock on X/social media.
Be direct and factual. Return ONLY valid JSON, no other text."""

_SENTIMENT_USER = """What is the current social media / X sentiment for ${ticker}?

Look at:
- Recent X posts, threads, and discussions about ${ticker}
- Retail trader sentiment (WSB, StockTwits-style chatter)
- Any notable mentions, trending discussions, or viral posts
- Ratio of bullish vs bearish sentiment in the last 24-48 hours

Return JSON with these exact fields:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "sentiment_score": float from -1.0 (max bearish) to 1.0 (max bullish),
  "buzz_level": "high" | "medium" | "low",
  "summary": "1 sentence — what are traders saying and why",
  "notable": "any specific catalyst, meme, or narrative driving the chatter (or null)"
}}"""

_BATCH_SENTIMENT_SYSTEM = """You are a social sentiment analyst for a professional trading system.
You have access to real-time X (Twitter) data. For each ticker provided,
assess the current social media sentiment from X posts and trader discussions.
Return ONLY valid JSON, no other text."""

_BATCH_SENTIMENT_USER = """Assess current X / social media sentiment for these stocks: {tickers}

For each ticker, return sentiment based on what traders are actually saying on X right now.

Return a JSON object where each key is a ticker symbol:
{{
  "TICKER1": {{
    "sentiment": "bullish" | "bearish" | "neutral",
    "sentiment_score": float -1.0 to 1.0,
    "buzz_level": "high" | "medium" | "low",
    "summary": "1 sentence max"
  }},
  "TICKER2": {{ ... }}
}}"""


_MARKET_PULSE_SYSTEM = """You are a real-time market intelligence analyst with live access to X (Twitter).
Your job is to report what traders and investors are actively discussing on X right now.
Be specific — name tickers, sectors, and themes. Return ONLY valid JSON, no other text."""

_MARKET_PULSE_USER = """What are traders on X talking about RIGHT NOW in the stock market?

Look at the last 1-2 hours of X posts. Report:
- Top tickers generating the most buzz (price action, news, unusual moves)
- Dominant sector themes (e.g. AI, energy, biotech catalysts)
- Overall market sentiment (risk-on / risk-off / mixed)
- Any breaking news or catalysts driving discussion

Return JSON with these exact fields:
{{
  "overall_sentiment": "bullish" | "bearish" | "mixed",
  "market_mood": "risk-on" | "risk-off" | "mixed",
  "top_tickers": [
    {{"ticker": "SYMBOL", "buzz": "why it's trending in 1 sentence", "sentiment": "bullish"|"bearish"|"neutral"}},
    ... up to 6 tickers
  ],
  "top_themes": ["theme1", "theme2", "theme3"],
  "summary": "2-3 sentence synthesis of what's driving market conversation on X right now"
}}"""


def get_market_pulse() -> dict:
    """
    Ask Grok what traders on X are talking about right now.
    Returns a dict with overall_sentiment, top_tickers, top_themes, summary.
    Returns empty dict on failure.
    """
    try:
        grok = _get_grok()
        if not grok:
            return {}
        result = grok.chat(
            system=_MARKET_PULSE_SYSTEM,
            user=_MARKET_PULSE_USER,
            json_mode=True,
        )
        parsed = result.get("parsed") or {}
        return parsed
    except Exception:
        return {}


def _get_grok():
    try:
        from analysis.grok_client import GrokClient
        return GrokClient()
    except Exception:
        return None


def query_ticker_sentiment(ticker: str) -> dict:
    """
    Ask Grok what X is saying about a ticker right now.

    Returns dict with:
      sentiment: bullish | bearish | neutral
      sentiment_score: -1.0 to 1.0
      buzz_level: high | medium | low
      summary: 1-sentence X chatter summary
      notable: notable catalyst/narrative or None
      source: grok | unavailable
    """
    grok = _get_grok()
    if not grok:
        return {"sentiment": "neutral", "sentiment_score": 0.0,
                "buzz_level": "low", "summary": "Grok unavailable",
                "notable": None, "source": "unavailable"}

    try:
        resp = grok.chat(
            system=_SENTIMENT_SYSTEM,
            user=_SENTIMENT_USER.replace("${ticker}", ticker.upper()),
            json_mode=True,
            temperature=0.2,
            max_tokens=300,
        )
        parsed = resp.get("parsed") or {}
        return {
            "sentiment": parsed.get("sentiment", "neutral"),
            "sentiment_score": float(parsed.get("sentiment_score", 0.0)),
            "buzz_level": parsed.get("buzz_level", "low"),
            "summary": parsed.get("summary", ""),
            "notable": parsed.get("notable"),
            "source": "grok",
        }
    except Exception as e:
        return {"sentiment": "neutral", "sentiment_score": 0.0,
                "buzz_level": "low", "summary": f"Error: {str(e)[:60]}",
                "notable": None, "source": "error"}


def run_social_buzz_scan(tickers: Optional[List[str]] = None,
                         batch_size: int = 10) -> List[dict]:
    """
    Batch sentiment scan for a list of tickers using Grok X access.

    If tickers is None, loads from social_buzz table (tickers already tracked).
    Writes sentiment + rank_surge back to Supabase.
    Returns list of result dicts.

    Designed to run once daily after market close.
    """
    conn = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from screen.reversal_screen import _conn
        conn = _conn()
    except Exception:
        pass

    # Load tickers from social_buzz if not provided
    if tickers is None:
        tickers = _load_buzz_tickers(conn)

    if not tickers:
        return []

    grok = _get_grok()
    results = []
    today = date.today()

    # Process in batches to manage Grok API calls
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        batch_results = _score_batch(batch, grok)

        for ticker, data in batch_results.items():
            result = {"ticker": ticker, "date": today, **data}
            results.append(result)

            # Write back to Supabase
            if conn:
                _upsert_sentiment(conn, ticker, today, data)

        time.sleep(0.5)  # pace Grok calls between batches

    return results


def _load_buzz_tickers(conn) -> List[str]:
    """Load tickers from the most recent social_buzz snapshot."""
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ticker FROM social_buzz
                WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM social_buzz)
                  AND mentions >= 5
                ORDER BY ticker
            """)
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


def _score_batch(tickers: List[str], grok) -> Dict[str, dict]:
    """Score a batch of tickers via a single Grok call."""
    if not grok:
        return {t: {"sentiment": "neutral", "sentiment_score": 0.0,
                    "buzz_level": "low", "summary": ""} for t in tickers}

    tickers_str = ", ".join(f"${t}" for t in tickers)
    try:
        resp = grok.chat(
            system=_BATCH_SENTIMENT_SYSTEM,
            user=_BATCH_SENTIMENT_USER.format(tickers=tickers_str),
            json_mode=True,
            temperature=0.2,
            max_tokens=1500,
        )
        parsed = resp.get("parsed") or {}
        results = {}
        for ticker in tickers:
            data = parsed.get(ticker, {})
            results[ticker] = {
                "sentiment": data.get("sentiment", "neutral"),
                "sentiment_score": float(data.get("sentiment_score", 0.0)),
                "buzz_level": data.get("buzz_level", "low"),
                "summary": data.get("summary", ""),
            }
        return results
    except Exception:
        return {t: {"sentiment": "neutral", "sentiment_score": 0.0,
                    "buzz_level": "low", "summary": ""} for t in tickers}


def _upsert_sentiment(conn, ticker: str, snapshot_date, data: dict) -> None:
    """Write Grok sentiment back to social_buzz table."""
    try:
        with conn.cursor() as cur:
            # Also compute rank_surge from existing row
            cur.execute("""
                UPDATE social_buzz
                SET sentiment       = %s,
                    sentiment_score = %s,
                    grok_summary    = %s,
                    rank_surge      = CASE
                        WHEN rank_24h_ago IS NOT NULL AND rank IS NOT NULL
                        THEN rank_24h_ago - rank
                        ELSE NULL
                    END
                WHERE ticker        = %s
                  AND snapshot_date = %s
            """, (
                data.get("sentiment"),
                data.get("sentiment_score"),
                data.get("summary", ""),
                ticker,
                snapshot_date,
            ))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def format_buzz_for_display(ticker: str, buzz_data: dict) -> str:
    """Format social buzz data for display in analyze_ticker output."""
    sentiment = buzz_data.get("sentiment", "neutral")
    score = buzz_data.get("sentiment_score", 0.0)
    buzz_level = buzz_data.get("buzz_level", "low")
    summary = buzz_data.get("summary", "")
    notable = buzz_data.get("notable")

    emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(sentiment, "⚪")
    buzz_emoji = {"high": "🔥", "medium": "📢", "low": "💤"}.get(buzz_level, "💤")

    line = f"{emoji} {sentiment.upper()} (score: {score:+.2f}) | Buzz: {buzz_emoji} {buzz_level}"
    if summary:
        line += f"\n  → {summary}"
    if notable:
        line += f"\n  📌 {notable}"
    return line


def get_social_buzz_from_db(conn, tickers: List[str]) -> Dict[str, dict]:
    """
    Load most recent social_buzz rows for a list of tickers.
    Used by screens to incorporate social signal without a live Grok call.
    """
    out: Dict[str, dict] = {}
    if not conn or not tickers:
        return out
    placeholders = ",".join(["%s"] * len(tickers))
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT DISTINCT ON (ticker)
                    ticker, sentiment, sentiment_score, rank, rank_surge,
                    mentions, mentions_24h_ago, grok_summary
                FROM social_buzz
                WHERE ticker IN ({placeholders})
                ORDER BY ticker, snapshot_date DESC
            """, tickers)
            for row in cur.fetchall():
                t, sent, score, rank, surge, mentions, mentions_24h, summary = row
                out[t] = {
                    "sentiment": sent or "neutral",
                    "sentiment_score": float(score) if score else 0.0,
                    "rank": rank,
                    "rank_surge": float(surge) if surge else 0.0,
                    "mentions": mentions or 0,
                    "mentions_24h_ago": mentions_24h or 0,
                    "grok_summary": summary or "",
                }
    except Exception:
        pass
    return out
