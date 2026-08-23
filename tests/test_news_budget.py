"""The FMP data-budget cuts, pinned (2026-08-22).

Two usage warnings in a week (90% -> 96% of the rolling-30-day plan)
traced to the news path: every 5-minute scan refetched up to 1,000
articles from two FMP feeds with no cache. The cuts must hold or the
key gets suspended — and a suspended key dims catalyst detection and,
worse, stalls the economic calendar the binary-day skip reads.

  1. The default article limit is 250 (NEWS_FMP_LIMIT still overrides —
     the firehose is a config choice, not a redeploy).
  2. fetch_recent_news caches per lookback within a short TTL: a second
     call inside the window must NOT hit the network again, and must
     return an equal but INDEPENDENT list (callers mutate results).
  3. Distinct lookbacks cache separately (35-min screens vs the
     momentum scan's 990-min map must never cross-contaminate).
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import news_scanner  # noqa: E402


def test_default_limit_is_250():
    assert news_scanner.NEWS_FMP_LIMIT_DEFAULT == 250
    # The env override survives — the default is a budget, not a cap.
    src = open(news_scanner.__file__).read()
    assert "NEWS_FMP_LIMIT" in src


def test_cache_prevents_refetch_within_ttl_and_isolates_lookbacks():
    calls = {"n": 0}

    class _FakeClient:
        def list_ticker_news(self, **kw):
            calls["n"] += 1
            return []

    orig_client = news_scanner._get_polygon_client
    orig_fmp = news_scanner._fetch_fmp_news
    news_scanner._NEWS_CACHE.clear()
    try:
        news_scanner._get_polygon_client = lambda: _FakeClient()
        news_scanner._fetch_fmp_news = lambda cutoff: [{
            "tickers": ["TEST"], "headline": "h", "summary": "",
            "published_utc": "", "article_url": "u", "publisher": "p"}]

        first = news_scanner.fetch_recent_news(lookback_minutes=35)
        assert calls["n"] == 1 and len(first) == 1
        second = news_scanner.fetch_recent_news(lookback_minutes=35)
        assert calls["n"] == 1, "second call within TTL must not refetch"
        assert second == first
        second.append({"headline": "mutated"})
        third = news_scanner.fetch_recent_news(lookback_minutes=35)
        assert len(third) == 1, "cache must hand out copies, not the list"

        news_scanner.fetch_recent_news(lookback_minutes=990)
        assert calls["n"] == 2, "a different lookback is a different fetch"

        # Expired entries refetch.
        ts, arts = news_scanner._NEWS_CACHE[35]
        news_scanner._NEWS_CACHE[35] = (
            ts - dt.timedelta(seconds=news_scanner.NEWS_CACHE_TTL_S + 1), arts)
        news_scanner.fetch_recent_news(lookback_minutes=35)
        assert calls["n"] == 3
    finally:
        news_scanner._get_polygon_client = orig_client
        news_scanner._fetch_fmp_news = orig_fmp
        news_scanner._NEWS_CACHE.clear()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} test(s) passed.")
