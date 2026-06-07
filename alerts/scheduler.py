"""
Watchtower — Background scheduler for intraday alerts.
Runs intraday scan every 30 min, 7 AM–4 PM ET, Mon–Fri.
"""
import logging
from datetime import datetime

log = logging.getLogger(__name__)


def run_scheduled_scan():
    """Called by scheduler every 30 min during trading hours."""
    try:
        from screen.intraday_screen import run_screen
        from alerts.email_alerts import send_intraday_alert

        results = run_screen(min_score=40.0, broad=False)  # quality+watchlist universe
        if not results:
            log.info("[scheduler] No intraday signals above threshold.")
            return

        is_mkt = results[0].get("is_market_hours", True)
        mins = results[0].get("minutes_elapsed", 0)
        sent = send_intraday_alert(results, minutes_elapsed=mins, is_market_hours=is_mkt)
        log.info(f"[scheduler] Scan complete. {len(results)} signals. Email sent: {sent}")
    except Exception as e:
        log.error(f"[scheduler] Error: {e}")


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

    # Every 30 min, Mon–Fri, 7:00 AM – 4:00 PM ET
    scheduler.add_job(
        run_scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="7-15", minute="0,30", timezone=et),
        id="intraday_scan",
        replace_existing=True,
    )

    scheduler.start()
    log.info("[scheduler] Intraday alert scheduler started. Runs every 30 min, 7 AM–4 PM ET, Mon–Fri.")
    return scheduler
