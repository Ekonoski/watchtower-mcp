"""
Watchtower — Background scheduler for all automated alerts.

Intraday alerts: every 15-30 min, Mon-Fri 7 AM–4 PM ET.
Hidden gems (daily): once per day at 6:30 AM ET, Mon-Fri.
"""
import logging

log = logging.getLogger(__name__)


def run_scheduled_scan():
    """Intraday scan + news scan — called every 15-30 min during trading hours."""
    try:
        from screen.intraday_screen import run_screen
        from alerts.email_alerts import send_intraday_alert
        from analysis.news_scanner import run_news_scan

        # Run intraday price/volume scan
        results = run_screen(min_score=40.0)  # broad=True by default — full US market

        # Run news scan — always, even if no intraday signals
        news_alerts = []
        try:
            news_alerts = run_news_scan(lookback_minutes=35)
            log.info(f"[scheduler] News scan: {len(news_alerts)} catalysts found.")
        except Exception as e:
            log.warning(f"[scheduler] News scan error (non-fatal): {e}")

        if not results and not news_alerts:
            log.info("[scheduler] No intraday signals or news catalysts above threshold.")
            return

        # Add social buzz for top intraday signal tickers
        social_buzz_map = {}
        if results:
            try:
                from analysis.social_buzz import query_ticker_sentiment
                # Only fetch for top 5 signals to keep it fast
                for r in results[:5]:
                    ticker = r.get("ticker", "")
                    if ticker:
                        buzz = query_ticker_sentiment(ticker)
                        social_buzz_map[ticker] = buzz
                        r["social_buzz"] = buzz
                log.info(f"[scheduler] Social buzz fetched for {len(social_buzz_map)} tickers.")
            except Exception as e:
                log.warning(f"[scheduler] Social buzz error (non-fatal): {e}")

        is_mkt = results[0].get("is_market_hours", True) if results else True
        mins = results[0].get("minutes_elapsed", 0) if results else 0
        sent = send_intraday_alert(
            results,
            minutes_elapsed=mins,
            is_market_hours=is_mkt,
            news_alerts=news_alerts,
        )
        log.info(
            f"[scheduler] Scan complete. {len(results)} signals, "
            f"{len(news_alerts)} news catalysts. Email sent: {sent}"
        )
    except Exception as e:
        log.error(f"[scheduler] Intraday scan error: {e}")


def run_daily_social_scan():
    """
    Daily social buzz scan — runs at 4:30 PM ET after market close.
    Fetches Grok X sentiment for all tickers in social_buzz table,
    writes sentiment + rank_surge back to Supabase.
    """
    try:
        from analysis.social_buzz import run_social_buzz_scan
        log.info("[scheduler] Starting daily social buzz scan...")
        results = run_social_buzz_scan()
        log.info(f"[scheduler] Social buzz scan: {len(results)} tickers updated.")
    except Exception as e:
        log.error(f"[scheduler] Social buzz scan error: {e}")


def run_daily_gems_scan():
    """
    Daily hidden gems scan — runs once per day at 6:30 AM ET.
    Scans the full US market (~10k stocks) via Polygon for up-and-comer setups.
    Slower than intraday scan — only appropriate for daily cadence.
    """
    try:
        from screen.upcomer_screen import run_screen
        from alerts.email_alerts import send_hidden_gems_alert

        log.info("[scheduler] Starting daily hidden gems scan (full market universe)...")
        results = run_screen(min_score=35.0, top_n=15, with_synthesis=False)
        if not results:
            log.info("[scheduler] No hidden gems found above threshold today.")
            return

        # Fetch live X sentiment for each gem before emailing
        try:
            from analysis.social_buzz import query_ticker_sentiment
            for r in results:
                ticker = r.get("ticker", "")
                if ticker:
                    buzz = query_ticker_sentiment(ticker)
                    r["social_buzz"] = buzz
            log.info(f"[scheduler] Social buzz fetched for {len(results)} hidden gems.")
        except Exception as e:
            log.warning(f"[scheduler] Social buzz for gems error (non-fatal): {e}")

        sent = send_hidden_gems_alert(results)
        log.info(f"[scheduler] Hidden gems scan: {len(results)} gems found. Email sent: {sent}")
    except Exception as e:
        log.error(f"[scheduler] Hidden gems scan error: {e}")


def start_scheduler():
    """Start the APScheduler background scheduler. Call once at server startup."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz
    except ImportError:
        log.warning("[scheduler] apscheduler or pytz not installed — scheduled alerts disabled.")
        return None

    et = pytz.timezone("America/New_York")
    scheduler = BackgroundScheduler(timezone=et)

    # Pre-market: every 30 min, 7:00–9:15 AM ET
    # Fires at: 7:00, 7:30, 8:00, 8:30, 9:00
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="7-8", minute="0,30", timezone=et),
        id="intraday_scan_premarket",
        replace_existing=True,
    )
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="0", timezone=et),
        id="intraday_scan_900",
        replace_existing=True,
    )

    # Market open through noon: every 15 min, 9:30 AM–12:00 PM ET
    # Fires at: 9:30, 9:45, 10:00, 10:15, 10:30, 10:45, 11:00, 11:15, 11:30, 11:45, 12:00
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="9-11", minute="15,30,45,0", timezone=et),
        id="intraday_scan_morning_15min",
        replace_existing=True,
    )
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="12", minute="0", timezone=et),
        id="intraday_scan_noon",
        replace_existing=True,
    )

    # Afternoon: every 30 min, 12:30–4:00 PM ET
    # Fires at: 12:30, 13:00, 13:30, 14:00, 14:30, 15:00, 15:30
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="12-15", minute="30,0", timezone=et),
        id="intraday_scan_afternoon_30min",
        replace_existing=True,
    )

    # Daily social buzz scan — 4:30 PM ET, Mon-Fri (after market close)
    # Fetches Grok X sentiment for all tracked tickers, writes to social_buzz table.
    scheduler.add_job(
        run_daily_social_scan,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="30", timezone=et),
        id="daily_social_buzz",
        replace_existing=True,
    )

    # Daily hidden gems scan — 6:30 AM ET, Mon-Fri
    # Runs before market open so it's in your inbox before the day starts.
    # Full market scan takes longer so runs once, not repeatedly.
    scheduler.add_job(
        run_daily_gems_scan,
        CronTrigger(day_of_week="mon-fri", hour="6", minute="30", timezone=et),
        id="daily_hidden_gems",
        replace_existing=True,
    )

    scheduler.start()
    log.info(
        "[scheduler] Scheduler started (America/New_York). "
        "Hidden gems: 6:30 AM daily → "
        "Pre-market intraday 30-min (7-9 AM) → "
        "15-min at open through noon (9:30 AM-12 PM) → "
        "30-min afternoon (12:30-4 PM ET)."
    )
    return scheduler
