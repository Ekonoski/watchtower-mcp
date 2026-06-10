"""
Watchtower — Background scheduler for all automated alerts.

Intraday scans: every 5 min during market hours (9:30 AM–4:00 PM ET),
every 15 min pre-market (7:00–9:15 AM ET), Mon-Fri. Every scan is persisted
for the live dashboard; email is gated so it only fires for fresh
high-conviction signals (override with EMAIL_EVERY_SCAN=true).
Hidden gems (daily): once per day at 6:30 AM ET, Mon-Fri.
"""
import logging
import os
import time
import traceback

log = logging.getLogger(__name__)

# ── Email gating ──────────────────────────────────────────────────────────────
# The dashboard sees every scan; email only fires when there's something new:
#   - an intraday signal scoring >= ALERT_EMAIL_MIN_SCORE on a ticker not
#     emailed in the last ALERT_EMAIL_COOLDOWN_MIN minutes, or
#   - a news catalyst on a ticker not emailed in that window, or
#   - nothing sent for an hour (heartbeat so you know it's alive).
EMAIL_EVERY_SCAN = os.environ.get("EMAIL_EVERY_SCAN", "").lower() in ("1", "true", "yes")
EMAIL_MIN_SCORE = float(os.environ.get("ALERT_EMAIL_MIN_SCORE", "55"))
EMAIL_COOLDOWN_SEC = int(os.environ.get("ALERT_EMAIL_COOLDOWN_MIN", "60")) * 60
EMAIL_HEARTBEAT_SEC = int(os.environ.get("ALERT_EMAIL_HEARTBEAT_MIN", "60")) * 60

_last_email_ts = 0.0
_emailed_tickers: dict = {}  # ticker -> unix ts last included in an email


def _fresh_for_email(results: list, news_alerts: list) -> tuple:
    """Return (fresh_signal_tickers, fresh_news_tickers) not emailed recently."""
    now = time.time()
    for t, ts in list(_emailed_tickers.items()):
        if now - ts > EMAIL_COOLDOWN_SEC:
            del _emailed_tickers[t]
    fresh_signals = [
        r["ticker"] for r in results
        if r.get("score", 0) >= EMAIL_MIN_SCORE and r.get("ticker") not in _emailed_tickers
    ]
    fresh_news = [
        n["primary_ticker"] for n in news_alerts
        if n.get("primary_ticker") and n["primary_ticker"] not in _emailed_tickers
    ]
    return fresh_signals, fresh_news


def _another_scan_just_saved(window_sec: int = 120) -> bool:
    """During a deploy, Railway briefly runs old + new containers and each owns
    its per-container scheduler lock — both fire the same cron slot and the
    user gets double snapshots/emails. Scans take ~2-3 min, so the check runs
    just before save/email: the slower container sees the faster one's
    snapshot and stands down. (Checking at scan start would race — both
    containers fire within seconds, before either has saved.)"""
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM scan_snapshots "
                    "WHERE as_of > now() - make_interval(secs => %s)",
                    (window_sec,),
                )
                return (cur.fetchone() or [0])[0] > 0
        finally:
            conn.close()
    except Exception:
        return False  # table missing / DB down — don't block scanning


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

        # Add social buzz for top intraday signal tickers
        social_buzz_map = {}
        if results:
            try:
                from analysis.social_buzz import query_ticker_sentiment
                for r in results[:5]:
                    ticker = r.get("ticker", "")
                    if ticker:
                        buzz = query_ticker_sentiment(ticker)
                        social_buzz_map[ticker] = buzz
                        r["social_buzz"] = buzz
                log.info(f"[scheduler] Social buzz fetched for {len(social_buzz_map)} tickers.")
            except Exception as e:
                log.warning(f"[scheduler] Social buzz error (non-fatal): {e}")

        # Market pulse — what's trending on X right now (always, every scan)
        market_pulse = {}
        try:
            from analysis.social_buzz import get_market_pulse
            market_pulse = get_market_pulse()
            log.info(f"[scheduler] Market pulse fetched: {market_pulse.get('overall_sentiment', 'n/a')}")
        except Exception as e:
            log.warning(f"[scheduler] Market pulse error (non-fatal): {e}")

        # Deploy-overlap dedupe: if a sibling container already saved this
        # scan slot while we were scanning, stand down (no save, no email).
        if _another_scan_just_saved():
            log.info("[scheduler] Sibling container already saved this scan slot — standing down.")
            return

        # Persist the full scan for the live dashboard (always, even with no signals)
        try:
            from dashboard import store
            store.save_scan(results, news_alerts, market_pulse)
        except Exception as e:
            log.warning(f"[scheduler] Dashboard snapshot save error (non-fatal): {e}")

        is_mkt = results[0].get("is_market_hours", True) if results else True
        mins = results[0].get("minutes_elapsed", 0) if results else 0

        global _last_email_ts
        fresh_signals, fresh_news = _fresh_for_email(results, news_alerts)
        heartbeat_due = (time.time() - _last_email_ts) >= EMAIL_HEARTBEAT_SEC
        should_email = EMAIL_EVERY_SCAN or fresh_signals or fresh_news or heartbeat_due

        sent = False
        if should_email:
            sent = send_intraday_alert(
                results,
                minutes_elapsed=mins,
                is_market_hours=is_mkt,
                news_alerts=news_alerts,
                market_pulse=market_pulse,
            )
            if sent:
                _last_email_ts = time.time()
                for t in fresh_signals + fresh_news:
                    _emailed_tickers[t] = _last_email_ts
        else:
            # Email path also logs alerts for performance tracking — keep that
            # going on skipped scans (alert_log dedupes per ticker/type/day).
            try:
                from analysis.alert_tracker import log_alerts
                if results:
                    log_alerts(results, "intraday")
                if news_alerts:
                    log_alerts(news_alerts, "news")
            except Exception as e:
                log.warning(f"[scheduler] Alert logging error (non-fatal): {e}")

        log.info(
            f"[scheduler] Scan complete. {len(results)} signals, "
            f"{len(news_alerts)} news catalysts. "
            f"Fresh: {len(fresh_signals)} signals / {len(fresh_news)} news. "
            f"Email sent: {sent}"
        )
    except Exception as e:
        log.error(f"[scheduler] Intraday scan error: {e}\n{traceback.format_exc()}")


def run_daily_fill_returns():
    """
    Daily return fill — runs at 4:45 PM ET after market close.
    Fetches current prices for all tracked alerts and updates d-day return columns.
    """
    try:
        from analysis.alert_tracker import fill_daily_returns
        log.info("[scheduler] Starting daily alert return fill...")
        updated = fill_daily_returns()
        log.info(f"[scheduler] Alert return fill: {updated} rows updated.")
    except Exception as e:
        log.error(f"[scheduler] Alert return fill error: {e}")


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


def run_daily_screens_scan():
    """
    Daily scan of all longer-hold screens — runs at 6:00 AM ET before market open.
    Logs results to alert_log automatically (no email — data only).
    Covers: reversal, momentum, breakdown, insider burst.
    """
    try:
        from analysis.alert_tracker import log_alerts

        # Reversal screen
        try:
            from screen.reversal_screen import run_screen as reversal_screen
            results = reversal_screen(min_drawdown=15.0)
            if results:
                log_alerts(results, "reversal")
                log.info(f"[scheduler] Reversal scan: {len(results)} signals logged.")
        except Exception as e:
            log.warning(f"[scheduler] Reversal scan error: {e}")

        # Momentum screen
        try:
            from screen.momentum_screen import run_screen as momentum_screen
            results = momentum_screen(max_pullback=10.0)
            if results:
                log_alerts(results, "momentum")
                log.info(f"[scheduler] Momentum scan: {len(results)} signals logged.")
        except Exception as e:
            log.warning(f"[scheduler] Momentum scan error: {e}")

        # Breakdown screen
        try:
            from screen.breakdown_screen import run_screen as breakdown_screen
            results = breakdown_screen(broad=True)
            if results:
                log_alerts(results, "breakdown")
                log.info(f"[scheduler] Breakdown scan: {len(results)} signals logged.")
        except Exception as e:
            log.warning(f"[scheduler] Breakdown scan error: {e}")

        # Insider burst screen
        try:
            from screen.insider_burst_screen import run_screen as insider_screen
            results = insider_screen()
            if results:
                log_alerts(results, "insider")
                log.info(f"[scheduler] Insider scan: {len(results)} signals logged.")
        except Exception as e:
            log.warning(f"[scheduler] Insider scan error: {e}")

    except Exception as e:
        log.error(f"[scheduler] Daily screens scan error: {e}")


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
    scheduler = BackgroundScheduler(
        timezone=et,
        job_defaults={
            # If a scan overruns its slot (cold Grok cache after a deploy can
            # push 6-8 min), collapse the missed firings into one instead of
            # bursting stale scans when the worker frees up.
            "coalesce": True,
            "misfire_grace_time": 240,
            "max_instances": 1,
        },
    )

    # Pre-market: every 15 min, 7:00–9:15 AM ET
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="7-8", minute="*/15", timezone=et),
        id="intraday_scan_premarket",
        replace_existing=True,
    )
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="0,15", timezone=et),
        id="intraday_scan_premarket_9am",
        replace_existing=True,
    )

    # Market hours: every 5 min, 9:30 AM–4:00 PM ET. The dashboard gets every
    # scan; email is gated (see _fresh_for_email) so this cadence doesn't spam.
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="30,35,40,45,50,55", timezone=et),
        id="intraday_scan_open",
        replace_existing=True,
    )
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute="*/5", timezone=et),
        id="intraday_scan_market_5min",
        replace_existing=True,
    )
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="0", timezone=et),
        id="intraday_scan_close",
        replace_existing=True,
    )

    # Daily return fill — 4:45 PM ET, Mon-Fri (after market close + social scan)
    scheduler.add_job(
        run_daily_fill_returns,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="45", timezone=et),
        id="daily_fill_returns",
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

    # Daily screens scan — 6:00 AM ET, Mon-Fri (reversal, momentum, breakdown, insider)
    # Runs before gems so logging is staggered; no email — data-only logging to alert_log.
    scheduler.add_job(
        run_daily_screens_scan,
        CronTrigger(day_of_week="mon-fri", hour="6", minute="0", timezone=et),
        id="daily_screens_scan",
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
        "Daily screens 6:00 AM → Hidden gems 6:30 AM → "
        "Pre-market 15-min (7:00-9:15 AM) → "
        "Market hours 5-min (9:30 AM-4:00 PM ET, email gated)."
    )
    return scheduler
