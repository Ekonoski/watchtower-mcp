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

    scheduler.start()
    log.info(
        "[scheduler] Intraday alert scheduler started (America/New_York). "
        "Pre-market 30-min (7-9 AM) → 15-min at open through noon (9:30 AM-12 PM) "
        "→ 30-min afternoon (12:30-4 PM ET)."
    )
    return scheduler
