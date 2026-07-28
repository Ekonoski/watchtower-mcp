"""
Watchtower — Social buzz / X sentiment via Grok API.

Two jobs:
  1. query_ticker_sentiment(ticker) — ask Grok what X is saying about a stock
     right now. Returns sentiment, score, and a 1-sentence summary.
     Used on-demand in watchtower_analyze_ticker.

  2. run_social_buzz_scan(tickers) — batch sentiment scan for a list of tickers.
     Writes results back to Supabase social_buzz table with sentiment + rank_surge.
     Scheduled daily (runs after market close).

Real-time X/web access comes from xAI Agent Tools (server-side web_search +
x_search), not the model's training data — see _grok_live() below.
"""

import logging
import os
import sys
import time
from datetime import date
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)

_SENTIMENT_SYSTEM = """You are a social sentiment analyst for a professional trading system.
You have access to real-time X (Twitter) data. Analyze what traders and investors
are currently saying about the given stock on X/social media.
Be direct and factual. Return ONLY valid JSON, no other text."""

_SENTIMENT_USER = """What is the current social media / X sentiment for ${ticker}?

First identify the company behind the symbol ${ticker}. Chatter for recent IPOs or
well-known companies often uses the company NAME (e.g. "SpaceX") far more than the
cashtag (e.g. "$SPCX"), so search X for BOTH the symbol ${ticker} AND the company name.

Look at:
- Recent X posts, threads, and discussions about ${ticker} or the company by name
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
Recent IPOs and well-known names are often discussed by company name rather than cashtag —
search by both the symbol and the company name so newly-listed tickers aren't missed.

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


def _get_grok():
    try:
        from analysis.grok_client import GrokClient
        return GrokClient()
    except Exception:
        return None


# ── Agent Tools (live web/X search) with a circuit breaker ───────────────────
# query_ticker_sentiment / market pulse need real-time data, which Grok only
# has via the Agent Tools API (server-side web_search + x_search). If those
# calls fail on this xAI tier (e.g. wrong search model), trip a breaker and
# fall back to a plain chat — so a misconfig can't error on every single scan.
# Disable entirely with XAI_AGENT_TOOLS=0.
_AGENT_TOOLS_ON = os.environ.get("XAI_AGENT_TOOLS", "1").strip().lower() not in ("0", "false", "off", "no", "")
_AGENT_TOOLS_MAX_FAILS = 2
_agent_tools_fails = 0


def _agent_tools_available() -> bool:
    return _AGENT_TOOLS_ON and _agent_tools_fails < _AGENT_TOOLS_MAX_FAILS


def _grok_live(grok, system: str, user: str, search_max_tokens: int, **plain_kwargs) -> dict:
    """
    Get a live answer: try Agent Tools (web_search + x_search) first; on any
    failure trip the breaker and fall back to a plain chat. Returns the chat
    result dict ({"text", "parsed", ...}).
    """
    global _agent_tools_fails
    if grok is not None and _agent_tools_available():
        try:
            return grok.search_chat(system=system, user=user, max_output_tokens=search_max_tokens)
        except Exception:
            _agent_tools_fails += 1  # breaker: stop hammering a broken live path
    return grok.chat(system=system, user=user, **plain_kwargs)


def get_market_pulse() -> dict:
    """
    Ask Grok what traders on X are talking about right now.
    Returns a dict with overall_sentiment, top_tickers, top_themes, summary.
    Returns empty dict on failure.

    Cached for PULSE_TTL_SEC — at a 5-min scan cadence, re-querying X every
    scan multiplies Grok live-search spend for no informational gain.
    """
    import time as _time
    cached = _pulse_cache.get("pulse")
    if cached and _time.time() - cached[0] < PULSE_TTL_SEC:
        return cached[1]
    try:
        grok = _get_grok()
        if not grok:
            return {}
        result = _grok_live(
            grok,
            _MARKET_PULSE_SYSTEM,
            _MARKET_PULSE_USER,
            900,
            json_mode=True,
        )
        parsed = result.get("parsed") or {}
        if parsed:
            _pulse_cache["pulse"] = (_time.time(), parsed)
        return parsed
    except Exception:
        return {}


# Grok live-X-search results don't change meaningfully inside a scan interval.
# Cache pulse and per-ticker sentiment so the 5-min cadence doesn't multiply
# API spend. TTLs tunable via env.
PULSE_TTL_SEC = int(os.environ.get("GROK_PULSE_TTL_MIN", "10")) * 60
BUZZ_TTL_SEC = int(os.environ.get("GROK_BUZZ_TTL_MIN", "15")) * 60
_pulse_cache: dict = {}        # "pulse" -> (ts, result)
_buzz_cache: dict = {}         # ticker -> (ts, result)


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
    import time as _time
    ticker = ticker.upper().strip()
    cached = _buzz_cache.get(ticker)
    if cached and _time.time() - cached[0] < BUZZ_TTL_SEC:
        return cached[1]

    grok = _get_grok()
    if not grok:
        return {"sentiment": "neutral", "sentiment_score": 0.0,
                "buzz_level": "low", "summary": "Grok unavailable",
                "notable": None, "source": "unavailable"}

    try:
        resp = _grok_live(
            grok,
            _SENTIMENT_SYSTEM,
            _SENTIMENT_USER.replace("${ticker}", ticker.upper()),
            700,
            json_mode=True,
            temperature=0.2,
            max_tokens=300,
        )
        parsed = resp.get("parsed") or {}
        out = {
            "sentiment": parsed.get("sentiment", "neutral"),
            "sentiment_score": float(parsed.get("sentiment_score", 0.0)),
            "buzz_level": parsed.get("buzz_level", "low"),
            "summary": parsed.get("summary", ""),
            "notable": parsed.get("notable"),
            "source": resp.get("source", "grok"),
        }
        # Trim the cache before it grows unbounded across a long session
        if len(_buzz_cache) > 300:
            cutoff = _time.time() - BUZZ_TTL_SEC
            for k, (ts, _) in list(_buzz_cache.items()):
                if ts < cutoff:
                    del _buzz_cache[k]
        _buzz_cache[ticker] = (_time.time(), out)
        return out
    except Exception as e:
        # Log the WHOLE error and keep enough of it in the summary to be
        # self-diagnosing. The old [:60] cut xAI's 403 body at exactly the
        # point where it explains itself ("...has either used all available
        # credits or reached its monthly spending limit") — the outage then
        # took a day of log archaeology to attribute instead of one glance.
        _log.error(f"[social_buzz] Grok sentiment lookup failed for {ticker}: {e}")
        return {"sentiment": "neutral", "sentiment_score": 0.0,
                "buzz_level": "low", "summary": f"Error: {str(e)[:300]}",
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

    try:
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
    finally:
        # conn was leaking — this runs daily and each leaked conn holds a
        # Supabase pool slot until GC.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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
        resp = _grok_live(
            grok,
            _BATCH_SENTIMENT_SYSTEM,
            _BATCH_SENTIMENT_USER.format(tickers=tickers_str),
            1500,
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

    # A failed call is NOT a neutral reading. The error path returns
    # sentiment=neutral/score=0.0/buzz=low as placeholders; rendering those
    # as a headline puts "⚪ NEUTRAL (score: +0.00) | Buzz: 💤 low" on screen
    # for what is actually no data at all. Say unavailable instead.
    if buzz_data.get("source") in ("error", "unavailable"):
        return (f"⚠ Unavailable — no sentiment read for ${ticker}. "
                f"{summary or 'Grok returned no data.'} "
                "(This is a failed lookup, not a neutral reading.)")

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
