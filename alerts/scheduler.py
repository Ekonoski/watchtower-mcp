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

# ── Email gating ───────────────────────────────────────────────────────────────
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


def _claim_daily_job(job_name: str) -> bool:
    """Cross-container once-per-day claim for the daily jobs.

    The scheduler 'lock' is a PID file in each container's own tmpfs, so a
    Railway deploy overlapping a daily slot (e.g. 6:30 AM gems) ran the job in
    BOTH containers — double gems email, double Grok spend.
    _another_scan_just_saved() only protects the intraday scan. This claims
    (job_name, ET date) via INSERT ON CONFLICT: exactly one container wins.
    Fails OPEN (True) on DB trouble — a rare duplicate email beats silently
    never running the job."""
    try:
        from screen.reversal_screen import _conn
        try:
            from screen.market_calendar import et_now
            run_date = et_now().date()
        except Exception:
            from datetime import date as _date
            run_date = _date.today()
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS scheduler_job_claims (
                           job_name TEXT NOT NULL,
                           run_date DATE NOT NULL,
                           claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                           PRIMARY KEY (job_name, run_date)
                       )"""
                )
                cur.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (job_name, run_date),
                )
                won = cur.rowcount == 1
            conn.commit()
            if not won:
                log.info(f"[scheduler] {job_name}: already claimed for {run_date} "
                         f"by a sibling container — skipping.")
            return won
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"[scheduler] job-claim check failed ({e}) — running anyway.")
        return True


def _release_daily_job(job_name: str):
    """Give back a daily claim after a run that failed permanently.

    The claim is written BEFORE the run so sibling containers can't
    double-fire — which means a run that dies leaves today's slot
    claimed-but-empty and nothing retries until tomorrow (2026-08-10: the
    6:45 pattern scan died in a database brownout and the Patterns tab —
    and the 7:40 spec-writer — served Friday's rows all day). Best-effort:
    a failed release just means tomorrow's slot is the retry, same as
    before this existed."""
    try:
        from screen.reversal_screen import _conn
        try:
            from screen.market_calendar import et_now
            run_date = et_now().date()
        except Exception:
            from datetime import date as _date
            run_date = _date.today()
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM scheduler_job_claims "
                            "WHERE job_name = %s AND run_date = %s",
                            (job_name, run_date))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"[scheduler] claim release for {job_name} failed: {e}")


def _build_x_velocity_alerts(market_pulse: dict, results: list, news_alerts: list) -> list:
    """X velocity: tickers trending on X with NO scanner signal and NO news
    behind them — often the earliest tell (rumors, viral DD, halts being
    discussed before the wires print). Returns signal-shaped rows that ride
    the normal pipeline: dashboard, notifications, email gating, tracking."""
    pulse_tickers = market_pulse.get("top_tickers") or []
    if not pulse_tickers:
        return []

    known = {r.get("ticker") for r in results}
    known.update(n.get("primary_ticker") for n in news_alerts)
    # Perennial mega-cap chatter and index / non-equity symbols aren't "early"
    # anything (SPX/VIX/etc. also aren't tradeable and print $0).
    NOISE = {"SPY", "QQQ", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD",
             "SPX", "VIX", "NDX", "RUT", "DJI", "DIA", "IWM", "ES", "NQ"}

    candidates = []
    for t in pulse_tickers:
        ticker = (t.get("ticker") or "").upper().strip().lstrip("$")
        if not ticker or len(ticker) > 5 or ticker in known or ticker in NOISE:
            continue
        candidates.append((ticker, t))
    if not candidates:
        return []

    # Live price/volume for the candidates
    snap_map = {}
    try:
        from analysis.news_scanner import _fetch_snapshot_map
        snap_map = _fetch_snapshot_map([c[0] for c in candidates])
    except Exception as e:
        log.warning(f"[scheduler] X velocity snapshot fetch error: {e}")

    # Validate each pulse ticker against the SAME per-ticker buzz query the
    # dashboard drawer uses. The market pulse is a loose "most-mentioned" list —
    # big slow names (HD, DHR) land on it via broad portfolio chatter but rate
    # "low buzz" on a focused read. Requiring medium/high buzz here keeps the
    # X_VELOCITY tag consistent with what the drawer shows, so a name flagged as
    # "trending" genuinely is. (Same cache backs both calls.)
    from analysis.social_buzz import query_ticker_sentiment
    rows = []
    for ticker, t in candidates:
        snap = snap_map.get(ticker, {})
        price = snap.get("price", 0) or 0
        volume = int(snap.get("volume", 0) or 0)
        if price <= 0 or volume <= 0:
            continue  # non-tradeable / index symbol / dead name
        try:
            buzz = query_ticker_sentiment(ticker)
        except Exception:
            buzz = {}
        level = (buzz.get("buzz_level") or "low").lower()
        if level not in ("medium", "high"):
            continue  # not actually buzzing on a focused read — skip
        sentiment = (buzz.get("sentiment") or t.get("sentiment") or "neutral").lower()
        summary = buzz.get("summary") or t.get("buzz", "")
        rows.append({
            "ticker": ticker,
            "sleeve": "x_velocity",
            "signal_type": "X_VELOCITY",
            "score": 70.0 if level == "high" else 60.0,
            "rationale": f"Trending on X ({level} buzz, {sentiment}) with no signal/news — {summary}"[:200],
            "current_price": price,
            "change_pct": round(snap.get("change_pct", 0), 2),
            "vol_pace_ratio": round(snap.get("vol_ratio", 0), 2),
            "today_volume": volume,
            "gap_pct": 0.0,
            "above_vwap": False,
            "x_sentiment": sentiment,
            "social_buzz": buzz,
            "company_name": "",
            "sector": "",
        })
    return rows


def run_scheduled_scan(force: bool = False):
    """Intraday scan + news scan — called every 15-30 min during trading hours.

    Skips automatically on non-trading days (weekends/holidays) so we don't burn
    paid data-API calls when the market is closed. Manual triggers pass force=True.
    """
    if not force:
        try:
            from screen.market_calendar import is_trading_day
            if not is_trading_day():
                log.info("[scheduler] Scan skipped — market closed (weekend/holiday).")
                return
        except Exception:
            pass
    try:
        from concurrent.futures import ThreadPoolExecutor

        from screen.intraday_screen import run_screen
        from alerts.email_alerts import send_intraday_alert
        from analysis.news_scanner import run_news_scan

        # News classification (Grok-bound, 1-4 min) runs concurrently with the
        # price scan instead of after it — cuts total scan time roughly in half.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="news-scan")
        news_future = executor.submit(run_news_scan, 35)

        # Run intraday price/volume scan
        results = run_screen(min_score=40.0)  # broad=True by default — full US market

        news_alerts = []
        try:
            news_alerts = news_future.result(timeout=600)
            log.info(f"[scheduler] News scan: {len(news_alerts)} catalysts found.")
        except Exception as e:
            log.warning(f"[scheduler] News scan error (non-fatal): {e}")
        finally:
            executor.shutdown(wait=False)

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

        # X velocity — pulse tickers with no signal/news behind them
        try:
            xvel = _build_x_velocity_alerts(market_pulse, results, news_alerts)
            if xvel:
                results.extend(xvel)
                log.info(f"[scheduler] X velocity: {len(xvel)} early-chatter alerts: "
                         + ", ".join(r['ticker'] for r in xvel))
        except Exception as e:
            log.warning(f"[scheduler] X velocity error (non-fatal): {e}")

        # Watchlist × levels: names you're tracking crossing their starred
        # S/R levels since the last scan. Signal-shaped rows ride the normal
        # pipeline (dashboard, notification, email gating, tracking).
        try:
            from analysis.watchlist_levels import build_watchlist_level_alerts
            wl = build_watchlist_level_alerts()
            if wl:
                results.extend(wl)
                log.info(f"[scheduler] Watchlist levels: {len(wl)} level cross(es): "
                         + ", ".join(f"{r['ticker']} {r['signal_type']}" for r in wl))
        except Exception as e:
            log.warning(f"[scheduler] Watchlist levels error (non-fatal): {e}")

        # Gamma flip crosses: an index switching dealer-hedging regimes
        # since the last scan. Rare, high-context; rides the normal
        # pipeline like the level alerts above.
        try:
            from analysis.gex import build_gamma_flip_alerts
            gfa = build_gamma_flip_alerts()
            if gfa:
                results.extend(gfa)
                log.info(f"[scheduler] Gamma flips: "
                         + ", ".join(f"{r['ticker']} {r['signal_type']}"
                                     for r in gfa))
        except Exception as e:
            log.warning(f"[scheduler] Gamma flip error (non-fatal): {e}")

        # Pattern triggers: forming chart patterns (inverse H&S, flags,
        # triangles, wedges…) whose neckline/trigger line was crossed since
        # the last scan. Same signal-shaped ride through the pipeline.
        try:
            from analysis.pattern_scan import build_pattern_breakout_alerts
            pb = build_pattern_breakout_alerts()
            if pb:
                results.extend(pb)
                log.info(f"[scheduler] Pattern triggers: {len(pb)} cross(es): "
                         + ", ".join(f"{r['ticker']} {r['signal_type']}" for r in pb))
        except Exception as e:
            log.warning(f"[scheduler] Pattern triggers error (non-fatal): {e}")

        # Deploy-overlap dedupe: if a sibling container already saved this
        # scan slot while we were scanning, stand down (no save, no email).
        # Manual triggers (force=True) skip this: the user pressed Scan Now
        # and expects THIS scan's snapshot — standing down because a scheduled
        # scan happened to save within the window silently discarded the whole
        # 2-3 minute run (UI said "scan started" and nothing ever appeared).
        if not force and _another_scan_just_saved():
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

        # Log alerts for performance tracking BEFORE the email attempt, on every
        # scan. This used to live inside the email path gated on a successful
        # send, so a Gmail/Resend hiccup silently dropped that scan's signals
        # from alert_log — under-counting the performance stats for exactly the
        # high-conviction scans. alert_log dedupes per ticker/type/day.
        try:
            from analysis.alert_tracker import log_alerts
            if results:
                log_alerts(results, "intraday")
            if news_alerts:
                log_alerts(news_alerts, "news")
        except Exception as e:
            log.warning(f"[scheduler] Alert logging error (non-fatal): {e}")

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
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            log.info("[scheduler] Return fill skipped — market closed (no new bar).")
            return
    except Exception:
        pass
    if not _claim_daily_job("fill_returns"):
        return
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
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            log.info("[scheduler] Social buzz scan skipped — market closed.")
            return
    except Exception:
        pass
    if not _claim_daily_job("social_buzz"):
        return
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
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            log.info("[scheduler] Daily screens scan skipped — market closed.")
            return
    except Exception:
        pass
    if not _claim_daily_job("daily_screens"):
        return
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
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            log.info("[scheduler] Hidden gems scan skipped — market closed.")
            return
    except Exception:
        pass
    if not _claim_daily_job("gems_scan"):
        return
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


def run_daily_pattern_scan():
    """
    Daily chart-pattern scan — 6:45 AM ET, after the daily screens/gems.
    Weekly + daily timeframes from the DB (data through last night's close),
    then 4h via Polygon for the bounded candidate set. Results land in
    pattern_scan; the dashboard Patterns tab and the intraday trigger alerts
    read from there.
    """
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            log.info("[scheduler] Pattern scan skipped — market closed.")
            return
    except Exception:
        pass
    if not _claim_daily_job("pattern_scan"):
        return
    # Retry armor + claim release (2026-08-10): the 6:45 run died in a
    # database brownout AFTER claiming its slot, so nothing retried until
    # the next day — and the 7:40 spec-writer armed the whole swing book
    # off Friday's rows. Transient DB trouble gets three attempts five
    # minutes apart; a scan that still can't finish RELEASES the claim so
    # a restarted container (or the boot catch-up) can take the slot back.
    last_err = None
    for attempt in range(1, 4):
        try:
            from analysis.pattern_scan import run_pattern_scan
            log.info(f"[scheduler] Starting daily pattern scan (attempt {attempt}/3)...")
            counts = run_pattern_scan()
            log.info(f"[scheduler] Pattern scan done: {counts}")
            last_err = None
            break
        except Exception as e:
            last_err = e
            log.error(f"[scheduler] Pattern scan attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(300)
    if last_err is not None:
        log.error("[scheduler] Pattern scan failed all attempts — releasing "
                  "today's claim so a retry can take the slot")
        _release_daily_job("pattern_scan")
    _run_oscillator_scan_safe(include_daily_weekly=True)


def run_midday_pattern_scan():
    """4h-only pattern refresh — 12:45 PM ET. 4h structures evolve intraday;
    weekly/daily don't grow a new bar until the close, so they stay put."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    if not _claim_daily_job("pattern_scan_4h_midday"):
        return
    try:
        from analysis.pattern_scan import scan_4h
        n = scan_4h()
        log.info(f"[scheduler] Midday 4h pattern refresh: {n} patterns.")
    except Exception as e:
        log.error(f"[scheduler] Midday 4h pattern scan error: {e}")
    # Daily/weekly bars can't change intraday — refresh only the 4h/1h reads.
    _run_oscillator_scan_safe(include_daily_weekly=False)


def run_hourly_oscillator_refresh():
    """4h/1h oscillator refresh every hour through the session (Eric,
    2026-08-14: "we have up to date real time data" — and he's right; the
    bottleneck was never the data, it was the twice-a-day scan cadence.
    An hourly reversal state has a shelf life of hours: TXN and AORT were
    washes at the 12:45 stamp and had already bounced by 4 PM). Runs :05
    past the hour so the just-completed hourly bar is what gets read.
    Daily/weekly bars can't change intraday and stay untouched."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    _run_oscillator_scan_safe(include_daily_weekly=False)


def run_close_sync_and_restamp():
    """4:35 PM ET — the day's daily bars land the same evening, from the
    real-time source (Eric, 2026-08-14: "our data is realtime. that
    shouldn't be an issue ever again"). One Polygon grouped-daily call
    upserts the session's bars for known tickers, then the full fleet
    re-stamps so daily/weekly reads carry TODAY at review time instead
    of waiting on the 10 PM price-cron. A 0-row sync skips the re-stamp
    loudly — re-stamping yesterday's table would only dress up stale."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    try:
        from analysis.close_sync import sync_todays_closes
        n = sync_todays_closes()
        if n == 0:
            log.warning("[close-sync] nothing landed — skipping the 4:35 "
                        "re-stamp; the 10:45 settling pass will retry")
            return
    except Exception as e:
        log.error(f"[close-sync] failed: {e}")
        return
    _run_oscillator_scan_safe(include_daily_weekly=True)


def run_evening_oscillator_restamp():
    """Settling pass after the nightly price-cron (polygon_price_daily,
    ~10 PM ET) overwrites the Polygon close-sync rows with official
    closes. Also the safety net for a failed 4:35 sync: without it,
    Friday's close would not reach the screens until Monday 6:45 and a
    weekend review reads Thursday (found 2026-08-14, Eric: "why
    thursdays bars?"). Two slots (10:45, 11:30 PM ET) so a late ingest
    gets a second chance; the run is skipped honestly — with a log —
    while today's bars are absent, and the daily claim is only taken
    when a re-stamp actually runs."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    try:
        import datetime as _dt
        from zoneinfo import ZoneInfo
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT max(trade_date) FROM daily_prices")
                latest = cur.fetchone()[0]
        finally:
            conn.close()
        # Compare in ET — at 10:45 PM ET the server's UTC date is already
        # tomorrow, and a UTC compare would deem the ingest forever late.
        today_et = _dt.datetime.now(ZoneInfo("America/New_York")).date()
        if latest is None or latest < today_et:
            log.warning("[oscillator] evening re-stamp: today's daily bars "
                        f"not ingested yet (latest {latest}) — skipping slot")
            return
    except Exception as e:
        log.warning(f"[oscillator] evening re-stamp freshness check failed: {e}")
        return
    if not _claim_daily_job("oscillator_evening_restamp"):
        return
    _run_oscillator_scan_safe(include_daily_weekly=True)


def run_iv_snapshot_job():
    """Nightly ATM-IV / open-interest snapshot (5:35 PM ET) — grows
    Watchtower's own IV-rank history so option structure choices (spreads
    vs straight premium) get smarter every day it runs."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    if not _claim_daily_job("iv_snapshot"):
        return
    try:
        from analysis.options_picker import run_iv_snapshot
        res = run_iv_snapshot()
        log.info(f"[options] nightly IV snapshot: {res}")
    except Exception as e:
        log.error(f"[options] IV snapshot error: {e}")


def run_momentum_scan_job():
    """Momentum scanner pass (gappers / Ignition / continuation /
    earnings-gap). Skips non-trading days; never raises."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    try:
        from analysis.momentum_scan import run_momentum_scan
        res = run_momentum_scan()
        log.info(f"[momentum] scan pass: {res}")
    except Exception as e:
        log.error(f"[momentum] scan error: {e}")


def run_ticker_stats_job():
    """Nightly ticker_stats refresh: whole-market volume/close history via
    Polygon grouped-daily bars (feeds relvol / 2-week move / momo memory
    for every listed name), then floats — the FMP bulk endpoint when the
    plan allows it, with per-symbol fetches during scan passes covering
    the gap otherwise."""
    if not _claim_daily_job("ticker_stats"):
        return
    try:
        from analysis.momentum_scan import refresh_market_history
        res = refresh_market_history()
        log.info(f"[momentum] market history: {res}")
    except Exception as e:
        log.error(f"[momentum] market history error: {e}")
    try:
        from analysis.momentum_scan import refresh_ticker_stats
        res = refresh_ticker_stats()
        log.info(f"[momentum] ticker_stats floats: {res}")
    except Exception as e:
        log.error(f"[momentum] ticker_stats error: {e}")


def _seed_momentum_if_empty():
    """Deploy seed: populate float data + one scanner pass so the Momentum
    tab works immediately instead of waiting for the next cron slot."""
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM ticker_stats "
                            "WHERE float_shares IS NOT NULL LIMIT 1")
                stats_empty = cur.fetchone() is None
                cur.execute("SELECT 1 FROM ticker_stats "
                            "WHERE avg_vol_20d IS NOT NULL LIMIT 1")
                hist_empty = cur.fetchone() is None
                cur.execute("SELECT 1 FROM momentum_scan LIMIT 1")
                scan_empty = cur.fetchone() is None
        finally:
            conn.close()
        if hist_empty:
            from analysis.momentum_scan import refresh_market_history
            log.info("[momentum] seeding market history...")
            refresh_market_history()
        if stats_empty:
            from analysis.momentum_scan import refresh_ticker_stats
            log.info("[momentum] seeding ticker_stats floats...")
            refresh_ticker_stats()
        if scan_empty or stats_empty or hist_empty:
            from analysis.momentum_scan import run_momentum_scan
            log.info("[momentum] seeding first scan pass...")
            run_momentum_scan()
    except Exception as e:
        log.warning(f"[momentum] seed skipped: {e}")


def run_gex_job():
    """Nightly gamma-levels compute (walls / flip / net GEX) after the
    close, once fresh OI has settled. Regime label + S/R candidates for
    the next session."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    if not _claim_daily_job("gex_levels"):
        return
    try:
        from analysis.gex import run_gex_scan
        res = run_gex_scan()
        log.info(f"[gex] nightly levels: {res}")
    except Exception as e:
        log.error(f"[gex] nightly error: {e}")


def run_gex_morning_job():
    """8:15 AM ET full-universe gamma sweep — the authoritative daily map.
    OI settles overnight, so this run sees fresher positioning than the
    5:50 PM preview AND a chain free of yesterday's expired contracts
    (the OPEX-Friday phantom-wall problem). Stamped on today's session,
    ready before the open."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    if not _claim_daily_job("gex_levels_am"):
        return
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from analysis.gex import run_gex_scan
        session = datetime.now(ZoneInfo("America/New_York")).date()
        res = run_gex_scan(as_of=session)
        log.info(f"[gex] morning levels: {res}")
    except Exception as e:
        log.error(f"[gex] morning error: {e}")


def run_short_side_job():
    """6:20 PM ET: FINRA publishes the day's Reg SHO short-volume files
    around 6 PM — ingest them, then top up stale short-interest values
    (bi-monthly data, stale-first, capped)."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    if not _claim_daily_job("short_side"):
        return
    try:
        from analysis.short_side import (run_short_volume_update,
                                         run_short_interest_update)
        res = run_short_volume_update()
        log.info(f"[short] daily volume: {res}")
        res = run_short_interest_update()
        log.info(f"[short] SI: {res}")
    except Exception as e:
        log.error(f"[short] daily error: {e}")


def _seed_short_if_missing():
    """Deploy backstop: backfill short_volume_daily when sparse."""
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(DISTINCT as_of) FROM short_volume_daily")
                days = cur.fetchone()[0] or 0
        finally:
            conn.close()
        if days >= 20:
            return
        log.info("[short] history sparse — seeding FINRA backfill...")
        from analysis.short_side import run_short_volume_update
        res = run_short_volume_update()
        log.info(f"[short] seed: {res}")
    except Exception as e:
        log.warning(f"[short] seed skipped: {e}")


def run_vix_job():
    """4:40 PM ET: settle the day's VIX/VIX3M closes (backfills to
    2021-06-01 on first run). The intraday provisional update rides the
    gamma 15-minute job."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    if not _claim_daily_job("vix_update"):
        return
    try:
        from analysis.vix import run_vix_update
        res = run_vix_update()
        log.info(f"[vix] daily: {res}")
    except Exception as e:
        log.error(f"[vix] daily error: {e}")


def _seed_vix_if_missing():
    """Deploy backstop: populate vix_history (incl. the initial
    backfill) whenever the latest stored session is stale."""
    try:
        from analysis.vix import run_vix_update
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT max(as_of) FROM vix_history")
                last = cur.fetchone()[0]
        finally:
            conn.close()
        from datetime import date, timedelta
        if last is not None and last >= date.today() - timedelta(days=4):
            return
        log.info("[vix] history missing/stale — seeding...")
        res = run_vix_update()
        log.info(f"[vix] seed: {res}")
    except Exception as e:
        log.warning(f"[vix] seed skipped: {e}")


def run_gex_intraday_job():
    """Every 15 min during market hours: re-price the four index chains
    at current spot — live net GEX / regime on the dashboard plus the
    day-path history in gex_intraday. OI is fixed overnight, but the
    re-pricing moves the FURNITURE too: the max-gamma strike migrates
    and the flip walks as spot/vol travel (2026-08-18, proven on our
    own recorded day-paths — the CPI-day 775→780 wall walk, QQQ's flip
    walking 724.72→723.21). The drift check below turns those re-marks
    into Discord alerts formatted as Tape Bot slot values."""
    try:
        from screen.market_calendar import is_trading_day
        if not is_trading_day():
            return
    except Exception:
        pass
    try:
        from analysis.gex import run_gex_intraday
        res = run_gex_intraday()
        log.info(f"[gex] intraday tick: {res}")
    except Exception as e:
        log.error(f"[gex] intraday error: {e}")
    try:
        from alerts.gamma_drift import run_gamma_drift_check
        res = run_gamma_drift_check()
        if res and not res.get("off"):
            log.info(f"[drift] {res}")
    except Exception as e:
        log.warning(f"[drift] check failed (non-fatal): {e}")
    try:
        # Proximity pings ride the same tick (Eric, 2026-08-23): "tell
        # me when a ticker is trading at or near those levels."
        from alerts.gamma_prox import run_gamma_prox_check
        res = run_gamma_prox_check()
        if res and not res.get("off") and res.get("sent"):
            log.info(f"[gamma-prox] {res}")
    except Exception as e:
        log.warning(f"[gamma-prox] check failed (non-fatal): {e}")
    try:
        from analysis.vix import run_vix_update
        run_vix_update(intraday=True)
    except Exception as e:
        log.warning(f"[vix] intraday tick failed: {e}")


def _seed_gex_if_missing():
    """Deploy backstop mirroring the IV-snapshot seed: if the completed
    session has no gamma levels, compute them now (options chains still
    show the session's OI/greeks after hours)."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
        mins = now.hour * 60 + now.minute
        if not (mins >= 16 * 60 + 10 or mins < 9 * 60):
            return
        from analysis.options_picker import iv_session_date
        from analysis.gex import run_gex_scan
        session = iv_session_date()
        try:
            from screen.market_calendar import is_trading_day
            if not is_trading_day(session):
                return
        except Exception:
            pass
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM gex_levels WHERE as_of = %s "
                            "LIMIT 1", (session,))
                have = cur.fetchone() is not None
        finally:
            conn.close()
        if have:
            return
        log.info(f"[gex] levels missing for {session} — seeding...")
        res = run_gex_scan()
        log.info(f"[gex] seed: {res}")
    except Exception as e:
        log.warning(f"[gex] seed skipped: {e}")


def _seed_iv_snapshot_if_missing():
    """Deploy backstop for the 5:35 PM IV snapshot: if the just-closed
    session has no iv_history rows, run the snapshot now. Options don't
    trade overnight, so from the close until the next open the chain
    still shows that session's quotes — the snapshot stamps
    iv_session_date(), not the wall-clock date, so even a midnight
    deploy banks the right day. A crashed nightly job doesn't have to
    cost a day of IV-rank history."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
        mins = now.hour * 60 + now.minute
        # After-hours through pre-open: 4:10 PM onward, or before 9:00 AM.
        if not (mins >= 16 * 60 + 10 or mins < 9 * 60):
            return
        from analysis.options_picker import iv_session_date, run_iv_snapshot
        session = iv_session_date()
        try:
            from screen.market_calendar import is_trading_day
            if not is_trading_day(session):
                return
        except TypeError:
            pass   # calendar helper takes no args on older versions
        except Exception:
            pass
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM iv_history WHERE as_of = %s "
                            "LIMIT 1", (session,))
                have = cur.fetchone() is not None
        finally:
            conn.close()
        if have:
            return
        log.info(f"[options] IV snapshot missing for {session} — seeding...")
        res = run_iv_snapshot()
        log.info(f"[options] IV snapshot seed: {res}")
    except Exception as e:
        log.warning(f"[options] IV snapshot seed skipped: {e}")


def _run_oscillator_scan_safe(include_daily_weekly: bool = True):
    """Watchtower Oscillator scan, chained after each pattern scan so the
    structural-confluence bucket reads fresh pattern rows. Never raises —
    a broken oscillator pass must not take the pattern jobs down with it."""
    try:
        from analysis.oscillator import run_oscillator_scan
        log.info("[oscillator] scan starting "
                 f"({'full' if include_daily_weekly else '4h/1h refresh'})...")
        counts = run_oscillator_scan(include_daily_weekly=include_daily_weekly)
        log.info(f"[oscillator] scan done: {counts}")
    except Exception as e:
        log.error(f"[oscillator] scan error: {e}")


def _seed_oscillator_if_empty():
    """Deploy-time seeding: run a full scan when the table is empty (first
    deploy) OR when the signal definitions changed (SIGNALS_VERSION bump) —
    stored signal payloads must never lag the code, or a freshly shipped
    signal shows an empty screen until the next scheduled scan. The version
    claim is taken only AFTER a successful scan, so a failed run retries on
    the next boot."""
    try:
        from analysis.oscillator import SIGNALS_VERSION
        from screen.reversal_screen import _conn
        claim = f"osc_signals_v{SIGNALS_VERSION}"
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM oscillator_scan LIMIT 1")
                empty = cur.fetchone() is None
                cur.execute("SELECT 1 FROM scheduler_job_claims "
                            "WHERE job_name = %s AND run_date = DATE '2000-01-01'",
                            (claim,))
                new_signals = cur.fetchone() is None
        finally:
            conn.close()
        if empty or new_signals:
            log.info(f"[oscillator] seeding (empty={empty}, "
                     f"signals updated={new_signals})...")
            _run_oscillator_scan_safe(include_daily_weekly=True)
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO scheduler_job_claims (job_name, run_date)
                        VALUES (%s, DATE '2000-01-01')
                        ON CONFLICT DO NOTHING
                    """, (claim,))
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        log.warning(f"[oscillator] seed skipped: {e}")


def _seed_oscillator_backtest_if_empty():
    """One-time historical backtest of the oscillator entry signals (runs at
    deploy while the table is empty; a few minutes over the full universe).
    Signals are confirmed-bar/no-repaint, so the replay is honest — it sees
    exactly what the live scanner would have fired on each historical bar."""
    try:
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                # Re-run when empty OR when the event set predates the newest
                # event type (idempotent upsert refreshes old rows too).
                cur.execute("SELECT 1 FROM oscillator_backtest "
                            "WHERE signal_type = 'mf_round' LIMIT 1")
                need = cur.fetchone() is None
        finally:
            conn.close()
        if need:
            from analysis.oscillator_backtest import run_backtest
            log.info("[oscillator] backtest missing coil events — replaying...")
            res = run_backtest()
            log.info(f"[oscillator] backtest done: {res}")
    except Exception as e:
        log.warning(f"[oscillator] backtest seed skipped: {e}")


def _seed_spec_bars_aug7_if_missing():
    """One-shot: land 2026-08-07's real 15m bars for that day's spec tickers
    in paper_spec_bars. The shadow audit's reclaim entries were priced off
    reconstruction and a fabricated close reached a card labeled 'real'
    (TNDM, retracted 2026-08-08) — repricing happens from recorded tape or
    not at all. Runs here because this service holds the Polygon key; the
    date guard inside makes it a no-op forever after the first success."""
    try:
        import datetime as _dt
        from analysis.paper_trader import backfill_spec_bars
        n = backfill_spec_bars(_dt.date(2026, 8, 7))
        if n:
            log.info(f"[paper] Aug 7 spec-bar backfill stored {n} bars")
    except Exception as e:
        log.warning(f"[paper] Aug 7 spec-bar backfill skipped: {e}")


def _seed_pattern_backtest_if_empty():
    """Deploy-time replay refresh: re-runs whenever the stored rows predate
    the current measurement rules (BT_VERSION) — the audited failure mode
    was stats from a retired engine silently surviving version bumps. The
    v2 run covers ~2,500 names incl. delisted from 2021 on (~1-2h in this
    background thread; inserts land incrementally, so an interrupted run
    resumes on the next deploy). Feeds 'Est. resolution / DTE'."""
    try:
        from analysis.pattern_backtest import BT_VERSION
        from analysis.pattern_scan import ENGINE_VERSION
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                # Re-fire until the current (measurement, engine) pair has
                # written its completion marker — an engine bump re-grades
                # every pattern automatically, and an interrupted run
                # resumes (already-stored names skip).
                cur.execute("SELECT 1 FROM scheduler_job_claims "
                            "WHERE job_name = %s LIMIT 1",
                            (f"pattern_bt_v{BT_VERSION}_e{ENGINE_VERSION}"
                             "_complete",))
                need = cur.fetchone() is None
                # v5's whole point is the deep regime window. Replaying
                # before the deep-history backfill (watchtower repo,
                # nightly slices) has FINISHED would grade whichever half
                # of the sample happened to land first, stamp itself
                # v5-complete, and never revisit the shallow half — a
                # partial-coverage prior wearing a full-coverage label.
                # Gate on the backfill's own completion marker, nothing
                # softer; re-checks every service start.
                cur.execute("SELECT 1 FROM scheduler_job_claims "
                            "WHERE job_name = 'daily_history_backfill_complete' "
                            "LIMIT 1")
                history_ready = cur.fetchone() is not None
        finally:
            conn.close()
        if need and not history_ready:
            log.info(f"[patterns] v{BT_VERSION} replay deferred — deep-history "
                     "backfill not yet complete (no completion claim); "
                     "draining nightly")
            need = False
        if need:
            from analysis.pattern_backtest import run_pattern_backtests
            # Hold the replay outside regular trading hours: it is an
            # hours-long IO-heavy grind, and 2026-08-10 proved the small
            # instance degrades PLATFORM-WIDE under that load (statement
            # timeouts every minute for hours after the bulk backfill).
            # The live desk owns the daytime IO; the replay owns the night.
            import datetime as _dt
            import time as _time
            import zoneinfo as _zi
            _now = _dt.datetime.now(_zi.ZoneInfo("America/New_York"))
            if _now.weekday() < 5 and _now.time() <= _dt.time(16, 15):
                _hold = (_now.replace(hour=16, minute=15, second=0,
                                      microsecond=0) - _now).total_seconds()
                log.info(f"[patterns] replay needed but market hours — "
                         f"holding {_hold / 3600:.1f}h until after the close")
                _time.sleep(_hold)
            log.info("[patterns] backtest predates BT_VERSION — replaying...")
            # Retry loop: the replay is hours of work that has died twice
            # in one night to transient DB errors (boot-time connection
            # blip, statement timeout under load) — and a dead seed stays
            # dead until the next deploy. Each retry resumes: stored
            # tickers skip, so attempts only ever re-do the failed tail.
            for attempt in range(1, 5):
                try:
                    res = run_pattern_backtests()
                    log.info(f"[patterns] timing backtest done: {res}")
                    break
                except Exception as e:
                    log.warning(f"[patterns] backtest replay attempt "
                                f"{attempt}/4 failed: {e!r}"
                                + ("" if attempt == 4 else " — retrying in 5m"))
                    if attempt < 4:
                        _time.sleep(300)
    except Exception as e:
        log.warning(f"[patterns] backtest seed skipped: {e}")


def _seed_cipher_study_if_missing():
    """Boot-time runner for the cipher-at-episodes study (Eric, 2026-08-11):
    the live oscillator engine graded across every v6 replay episode. Runs
    ONLY outside market hours (the live desk owns daytime database I/O —
    same hold as the pattern replay), resumes per ticker across boots, and
    no-ops forever once its completion marker exists. Keep this LAST in
    _seed_all: its hold would otherwise block later seeders."""
    try:
        import datetime as _dt
        import time as _time
        import zoneinfo as _zi
        from analysis.cipher_episode_study import run, COMPLETE_MARKER
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s LIMIT 1",
                            (COMPLETE_MARKER,))
                if cur.fetchone():
                    return
        finally:
            conn.close()
        _now = _dt.datetime.now(_zi.ZoneInfo("America/New_York"))
        if _now.weekday() < 5 and _now.time() <= _dt.time(16, 15):
            _hold = (_now.replace(hour=16, minute=15, second=0, microsecond=0)
                     - _now).total_seconds()
            log.info(f"[cipher-study] needed but market hours — holding "
                     f"{_hold/3600:.1f}h until after the close")
            _time.sleep(_hold)
        # Up to four budget cycles (~3.7h) so one evening finishes the whole
        # study instead of stranding the tail until some future deploy.
        for _attempt in range(4):
            if run():
                break
    except Exception as e:
        log.warning(f"[scheduler] cipher study seed skipped: {e}")


def _seed_defense_study_if_missing():
    """Boot-time runner for the 15m defense study (Eric, 2026-08-21):
    the defended-entry signature graded at historical retest episodes
    (pattern_backtest.retest_bar + Polygon 15m history). Same shape as
    the cipher study seeder: outside market hours only, resumes by
    episode across boots, no-ops forever once the marker exists. Keep
    beside the cipher seeder at the END of _seed_all."""
    try:
        import datetime as _dt
        import time as _time
        import zoneinfo as _zi
        from analysis.defense_study import COMPLETE_MARKER, run
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s LIMIT 1",
                            (COMPLETE_MARKER,))
                if cur.fetchone():
                    return
        finally:
            conn.close()
        _now = _dt.datetime.now(_zi.ZoneInfo("America/New_York"))
        if _now.weekday() < 5 and _now.time() <= _dt.time(16, 15):
            _hold = (_now.replace(hour=16, minute=15, second=0, microsecond=0)
                     - _now).total_seconds()
            log.info(f"[defense-study] needed but market hours — holding "
                     f"{_hold/3600:.1f}h until after the close")
            _time.sleep(_hold)
        # 2026-08-21, first run: the eligible pool was 22k episodes, not
        # the 1.2k the sample cap imagined — four passes stranded 17.5k.
        # Run until the pool is dry or ~6 hours pass; resume next boot.
        _t0 = _time.time()
        while _time.time() - _t0 < 6 * 3600:
            if run():
                break
    except Exception as e:
        log.warning(f"[scheduler] defense study seed skipped: {e}")


def _seed_sector_study_if_missing():
    """Boot-time runner for the sector-rotation study (Eric, 2026-08-22):
    every graded daily bullish episode joined to its sector's breadth
    read on its own breakout date — entirely from recorded tables, no
    external fetches. Defense-study shape: after-hours hold, resumes by
    month/episode across boots, marker retires it."""
    try:
        import datetime as _dt
        import time as _time
        import zoneinfo as _zi
        from analysis.sector_study import COMPLETE_MARKER, run
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s LIMIT 1",
                            (COMPLETE_MARKER,))
                if cur.fetchone():
                    return
        finally:
            conn.close()
        _now = _dt.datetime.now(_zi.ZoneInfo("America/New_York"))
        if _now.weekday() < 5 and _now.time() <= _dt.time(16, 15):
            _hold = (_now.replace(hour=16, minute=15, second=0, microsecond=0)
                     - _now).total_seconds()
            log.info(f"[sector-study] needed but market hours — holding "
                     f"{_hold/3600:.1f}h until after the close (DB-heavy)")
            _time.sleep(_hold)
        _t0 = _time.time()
        while _time.time() - _t0 < 4 * 3600:
            if run():
                break
    except Exception as e:
        log.warning(f"[scheduler] sector study seed skipped: {e}")


def _seed_structure_screen_if_empty():
    """First-deploy fill for the structure screen (Eric, 2026-08-23) —
    DB-only work, safe at any boot hour. After the seed, the nightly
    23:05 job owns it."""
    try:
        from analysis.structure_screen import run_structure_screen
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM structure_screen LIMIT 1")
                if cur.fetchone():
                    return
        finally:
            conn.close()
        run_structure_screen()
    except Exception as e:
        log.warning(f"[scheduler] structure screen seed skipped: {e}")


def _seed_daybias_bars_if_missing():
    """Boot-time backfill of SPY/QQQ/IWM 15m history for the day-bias
    study (Eric, 2026-08-23). ~33 Polygon calls total, resumable by
    max-stored-date, marker-retired. Runs at any boot hour — the calls
    are few and the inserts are boot-thread work."""
    try:
        from analysis.daybias_bars import COMPLETE_MARKER, run
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s LIMIT 1",
                            (COMPLETE_MARKER,))
                if cur.fetchone():
                    return
        finally:
            conn.close()
        import time as _time
        _t0 = _time.time()
        while _time.time() - _t0 < 2 * 3600:
            if run():
                break
    except Exception as e:
        log.warning(f"[scheduler] daybias bars seed skipped: {e}")


def _seed_defense_retro_if_missing():
    """One-shot retro defense read (Eric, 2026-08-22): the desk's own
    past touch fills graded against the defense signature — research,
    not ledger (its verdicts live in defense_retro, never in
    paper_defense_shadow). ~45 trades, a couple of Polygon calls each;
    small enough to run at any boot hour. Marker retires it forever."""
    try:
        from analysis.defense_retro import COMPLETE_MARKER, run
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM scheduler_job_claims WHERE job_name=%s LIMIT 1",
                            (COMPLETE_MARKER,))
                if cur.fetchone():
                    return
        finally:
            conn.close()
        for _attempt in range(3):   # tiny pool; a retry pass covers blips
            if run():
                break
    except Exception as e:
        log.warning(f"[scheduler] defense retro seed skipped: {e}")


def _run_missed_daily_pattern_scan():
    """Boot-time catch-up for a dead 6:45 scan (2026-08-10): the scan claimed
    its slot, died in the database brownout, and nothing retried — the 7:40
    spec-writer armed the whole swing book from Friday-stale rows (TNDM's
    trigger sat 23% below the market it woke up to). On boot during a
    trading day, if the 6:45 slot has passed and pattern_scan holds no row
    written today (ET), take the claim — retaking one orphaned by a run
    that died >30 minutes ago without writing — and scan now. Runs AFTER
    _seed_pattern_scan_if_stale so a seed rescan satisfies the check."""
    try:
        import datetime as _dtm
        from screen.market_calendar import is_trading_day, et_now
        now = et_now()
        if not is_trading_day() or now.time() < _dtm.time(6, 45):
            return
        from screen.reversal_screen import _conn
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT max(scanned_at AT TIME ZONE 'America/New_York')
                               FROM pattern_scan""")
                row = cur.fetchone()
                last = row[0] if row else None
                if last is not None and last.date() >= now.date():
                    return          # today's scan wrote — nothing to heal
                cur.execute(
                    """INSERT INTO scheduler_job_claims (job_name, run_date)
                       VALUES ('pattern_scan', %s)
                       ON CONFLICT (job_name, run_date) DO UPDATE
                       SET claimed_at = now()
                       WHERE scheduler_job_claims.claimed_at < now() - interval '30 minutes'""",
                    (now.date(),))
                won = cur.rowcount == 1
            conn.commit()
        finally:
            conn.close()
        if not won:
            log.info("[scheduler] missed-scan catch-up: a sibling holds a "
                     "live claim — standing down")
            return
        log.warning(f"[scheduler] pattern_scan last wrote {last} — today's "
                    "6:45 scan never landed; running catch-up scan now")
        from analysis.pattern_scan import run_pattern_scan
        counts = run_pattern_scan()
        log.info(f"[scheduler] Catch-up pattern scan done: {counts}")
    except Exception as e:
        log.error(f"[scheduler] missed-scan catch-up failed: {e}")


def _seed_pattern_scan_if_stale():
    """Deploy-time seeding: run a full pattern scan right away (even on a
    weekend) when the table has never been populated OR this deploy ships a
    newer detection engine (ENGINE_VERSION bump) — new/changed patterns show
    up within minutes instead of waiting for the next 6:45 AM slot. The
    version marker is a one-shot claim (sentinel date) so overlapping deploy
    containers can't both run it."""
    try:
        from analysis.pattern_scan import run_pattern_scan, ENGINE_VERSION
        from screen.reversal_screen import _conn
        need = False
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS scheduler_job_claims (
                           job_name TEXT NOT NULL,
                           run_date DATE NOT NULL,
                           claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                           PRIMARY KEY (job_name, run_date)
                       )"""
                )
                cur.execute(
                    "INSERT INTO scheduler_job_claims (job_name, run_date) "
                    "VALUES (%s, DATE '2000-01-01') ON CONFLICT DO NOTHING",
                    (f"pattern_engine_v{ENGINE_VERSION}",),
                )
                if cur.rowcount == 1:
                    need = True  # first container to boot this engine version
                else:
                    cur.execute("SELECT 1 FROM pattern_scan LIMIT 1")
                    need = cur.fetchone() is None
                    if not need:
                        # Liveness takeover: the claim is written BEFORE the
                        # run (so sibling containers don't double-scan), and
                        # the exception path releases it — but a container
                        # KILLED mid-scan (a rapid follow-up deploy replacing
                        # it) never reaches that path, and the orphaned claim
                        # would block this engine version forever (7/12: the
                        # #100 deploy killed #99's v8 rescan at +7 min; every
                        # later boot skipped it). If the claim is old enough
                        # that a healthy scan would have finished AND nothing
                        # has been written since it was taken, re-claim and
                        # re-run.
                        cur.execute(
                            """
                            SELECT 1 FROM scheduler_job_claims c
                            WHERE c.job_name = %s AND c.run_date = DATE '2000-01-01'
                              AND c.claimed_at < now() - interval '20 minutes'
                              AND COALESCE((SELECT max(scanned_at) FROM pattern_scan),
                                           TIMESTAMPTZ 'epoch') < c.claimed_at
                            """,
                            (f"pattern_engine_v{ENGINE_VERSION}",),
                        )
                        if cur.fetchone() is not None:
                            cur.execute(
                                "UPDATE scheduler_job_claims SET claimed_at = now() "
                                "WHERE job_name = %s AND run_date = DATE '2000-01-01'",
                                (f"pattern_engine_v{ENGINE_VERSION}",),
                            )
                            need = True
                            log.warning("[scheduler] pattern engine claim was "
                                        "orphaned by an interrupted scan — "
                                        "re-claiming and re-running")
            conn.commit()
        finally:
            conn.close()
        if not need:
            return
        log.info("[scheduler] pattern_scan empty or engine updated — full rescan...")
        try:
            counts = run_pattern_scan()
            log.info(f"[scheduler] Pattern seed scan done: {counts}")
        except Exception as e:
            # The version marker was claimed BEFORE the run (so sibling
            # containers don't double-scan). If the run itself fails — a slow
            # query tripping the statement timeout, a transient DB error — the
            # claim would otherwise permanently block this version from ever
            # retrying on a later deploy. Release it so the next boot retries.
            log.error(f"[scheduler] Pattern seed scan failed, releasing claim: {e}")
            try:
                from analysis.pattern_scan import ENGINE_VERSION as _ev
                c2 = _conn()
                try:
                    with c2.cursor() as cur:
                        cur.execute("DELETE FROM scheduler_job_claims "
                                    "WHERE job_name = %s AND run_date = DATE '2000-01-01'",
                                    (f"pattern_engine_v{_ev}",))
                    c2.commit()
                finally:
                    c2.close()
            except Exception as e2:
                log.warning(f"[scheduler] claim release failed: {e2}")
    except Exception as e:
        log.warning(f"[scheduler] Pattern seed scan skipped: {e}")


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

    # Paper trader (measurement engine): specs at 7:40 after the gamma sweep,
    # trigger loop on the market-hours cadence. Both no-ops on weekends and
    # write only to paper_specs / paper_trades — no orders, no alerts.
    def _paper_specs():
        from analysis.paper_trader import write_morning_specs
        write_morning_specs()

    def _paper_loop():
        # Errors land in ingestion_log (2026-08-24: the trigger loop
        # died silently at the 9:35 open — no RTH bars, no fills, stops
        # unwatched — and only stdout knew. The day-bias lesson applied
        # to the desk's most important job.)
        try:
            from analysis.paper_trader import run_trigger_loop
            run_trigger_loop()
        except Exception:
            import traceback
            log.exception("[paper] trigger loop failed")
            try:
                from screen.reversal_screen import _conn
                conn = _conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO ingestion_log
                               (job_name, status, started_at, completed_at,
                                records_processed, errors_count, error_summary)
                               VALUES ('paper_trigger_loop','error',now(),now(),
                                       0,1,%s)""",
                            (traceback.format_exc()[-900:],))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass

    scheduler.add_job(
        _paper_specs,
        CronTrigger(day_of_week="mon-fri", hour="7", minute="40", timezone=et),
        id="paper_specs_morning", replace_existing=True,
    )
    scheduler.add_job(
        _paper_loop,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="35,40,45,50,55", timezone=et),
        id="paper_loop_open", replace_existing=True,
    )
    scheduler.add_job(
        _paper_loop,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute="*/5", timezone=et),
        id="paper_loop_market", replace_existing=True,
    )

    # Closing-bar capture (2026-08-11): the loop's last pass runs by 15:58,
    # before the final 15m bar completes — one persistence-only pass at
    # 16:07 records the day's most important bar for every tracked ticker.
    def _paper_closing_bars():
        from analysis.paper_trader import persist_closing_bars
        persist_closing_bars()

    scheduler.add_job(
        _paper_closing_bars,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="7", timezone=et),
        id="paper_closing_bars", replace_existing=True,
    )

    # Swing true-close settle (2026-08-15, the AGMB 0.9-cent case): the
    # loop's 'daily close' was the 15:30–15:45 bar, so a stop broken only
    # in the final 15 minutes never fired. Reads the recorded closing bar
    # persisted at 16:07 — never refetches.
    def _paper_swing_settle():
        from analysis.paper_trader import run_swing_close_settle
        run_swing_close_settle()

    scheduler.add_job(
        _paper_swing_settle,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="20", timezone=et),
        id="paper_swing_settle", replace_existing=True,
    )

    # FVG snapshot (2026-08-13): imbalance zones persisted every morning so
    # the gamma board — and any session without the live engine — reads
    # them from the record (fvg_runs / fvg_zones) like every other board
    # input. 7:35: after the overnight daily bars have settled, before the
    # 7:40 spec writer reads the world. The Aug 13 board shipped this
    # section as a declared hole; persistence is the fix, not reachability.
    def _fvg_snapshot():
        from analysis.fvg import write_fvg_snapshot
        write_fvg_snapshot()

    scheduler.add_job(
        _fvg_snapshot,
        CronTrigger(day_of_week="mon-fri", hour="7", minute="35", timezone=et),
        id="fvg_snapshot_morning", replace_existing=True,
    )

    # Morning gamma sweep — 7:30 AM ET, full universe on overnight-settled
    # OI. The authoritative map for the day, ready while premarket prep is
    # underway. 7:30 is the practical floor: OCC/Polygon's overnight OI
    # refresh is reliably done by then; much earlier risks a stale read
    # (and the 9:35 intraday layer self-corrects the indexes regardless).
    scheduler.add_job(
        run_gex_morning_job,
        CronTrigger(day_of_week="mon-fri", hour="7", minute="30", timezone=et),
        id="gex_morning",
        replace_existing=True,
    )

    # Index gamma re-price: every 15 min during market hours + one read
    # just after the close (delayed feed is ~15 min behind the tape).
    scheduler.add_job(
        run_gex_intraday_job,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="35,50", timezone=et),
        id="gex_intraday_open",
        replace_existing=True,
    )
    scheduler.add_job(
        run_gex_intraday_job,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute="*/15", timezone=et),
        id="gex_intraday_market",
        replace_existing=True,
    )
    scheduler.add_job(
        run_gex_intraday_job,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="5", timezone=et),
        id="gex_intraday_close",
        replace_existing=True,
    )

    # Gamma drift baseline — 9:20 ET, before the 9:35 intraday upsert
    # overwrites gex_levels: capture the morning-board marks (the numbers
    # in Eric's TradingView slots) into gamma_drift_state. The drift
    # check itself rides every gex_intraday tick above.
    def _drift_baseline():
        from alerts.gamma_drift import seed_baseline
        seed_baseline()

    scheduler.add_job(
        _drift_baseline,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="20", timezone=et),
        id="gamma_drift_baseline", replace_existing=True,
    )

    # Desk event stream — the paper desk narrating fills/exits to Discord
    # (#desk). Polls the record on the trigger-loop cadence plus one pass
    # at 16:25 to catch the 16:20 settle verdicts. At-most-once per trade
    # event via discord_notify_log claims; no-ops when the webhook is
    # unset.
    def _desk_events():
        from alerts.desk_events import run_desk_event_notify
        run_desk_event_notify()

    scheduler.add_job(
        _desk_events,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="38,48,58", timezone=et),
        id="desk_events_open", replace_existing=True,
    )
    scheduler.add_job(
        _desk_events,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute="3,13,23,33,43,53", timezone=et),
        id="desk_events_market", replace_existing=True,
    )
    scheduler.add_job(
        _desk_events,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="25", timezone=et),
        id="desk_events_settle", replace_existing=True,
    )

    # Defended-entry shadow (2026-08-21): rides today's touch fills off
    # RECORDED bars — measurement only, never touches the live book.
    # Evaluates on the quarter-hours (after bars persist) plus a 16:30
    # pass so settle-day exits get their shadow_r graded same evening.
    def _defense_shadows():
        from analysis.defense_shadow import evaluate_defense_shadows
        evaluate_defense_shadows()
        # The retro cohort accrues at the same exits (Eric, 2026-08-22:
        # the pre-Monday touch fills carry forward as labeled data).
        try:
            from analysis.defense_retro import grade_at_exits
            grade_at_exits()
        except Exception as e:
            log.warning(f"[defense-retro] exit grading skipped: {e}")

    scheduler.add_job(
        _defense_shadows,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute="6,21,36,51", timezone=et),
        id="defense_shadow_market", replace_existing=True,
    )
    scheduler.add_job(
        _defense_shadows,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="30", timezone=et),
        id="defense_shadow_settle", replace_existing=True,
    )

    # Day-bias audition book (2026-08-23): SPY late-retest play, one
    # spec/day, measurement only. 5-min pass arms/fills/cancels on
    # recorded bars; 16:42 settle exits at the true close.
    def _daybias_loop():
        # Errors land in ingestion_log so they are diagnosable from the
        # record (2026-08-24: the first live morning produced no spec
        # and no readable error — a failure that only Railway's stdout
        # knew about is a hole in the record).
        try:
            from analysis.day_bias import run_daybias_loop
            run_daybias_loop()
        except Exception:
            import traceback
            log.exception("[day-bias] loop failed")
            try:
                from screen.reversal_screen import _conn
                conn = _conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO ingestion_log
                               (job_name, status, started_at, completed_at,
                                records_processed, errors_count, error_summary)
                               VALUES ('day_bias_loop','error',now(),now(),
                                       0,1,%s)""",
                            (traceback.format_exc()[-900:],))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass

    def _daybias_settle():
        try:
            from analysis.day_bias import run_daybias_settle
            run_daybias_settle()
        except Exception:
            log.exception("[day-bias] settle failed")

    scheduler.add_job(
        _daybias_loop,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=et),
        id="daybias_loop", replace_existing=True,
    )
    scheduler.add_job(
        _daybias_settle,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="42", timezone=et),
        id="daybias_settle", replace_existing=True,
    )

    # 📐 day-bias verdict ping (2026-08-25, Eric: "ok build the ping
    # tonight after close"): one #desk message per day stating
    # ARMED / STAND-ASIDE / unavailable, riding one minute behind the
    # book's own ticks so the 9:51 send reports the 9:50 decision; the
    # same pass announces an early-touch cancel the moment the record
    # shows it (2026-08-25's first cancelled_early happened silently).
    def _daybias_ping():
        try:
            from alerts.day_bias_ping import run_daybias_ping
            run_daybias_ping()
        except Exception:
            import traceback
            log.exception("[day-bias-ping] failed")
            try:
                from screen.reversal_screen import _conn
                conn = _conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO ingestion_log
                               (job_name, status, started_at, completed_at,
                                records_processed, errors_count, error_summary)
                               VALUES ('day_bias_ping','error',now(),now(),
                                       0,1,%s)""",
                            (traceback.format_exc()[-900:],))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass

    scheduler.add_job(
        _daybias_ping,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="51,56",
                    timezone=et),
        id="daybias_ping_open", replace_existing=True,
    )
    scheduler.add_job(
        _daybias_ping,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute="1-56/5",
                    timezone=et),
        id="daybias_ping_market", replace_existing=True,
    )

    # Intraday structure watcher (2026-08-24): follows the nightly
    # screen's freshest breakout/retest names on 15m bars; pings a
    # DEFENDED retest at major structure. Screen extension — arms
    # nothing; verdicts persist to structure_watch.
    def _structure_watch():
        try:
            from alerts.structure_watch import run_structure_watch
            run_structure_watch()
        except Exception:
            import traceback
            log.exception("[structure-watch] failed")
            try:
                from screen.reversal_screen import _conn
                conn = _conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO ingestion_log
                               (job_name, status, started_at, completed_at,
                                records_processed, errors_count, error_summary)
                               VALUES ('structure_watch','error',now(),now(),
                                       0,1,%s)""",
                            (traceback.format_exc()[-900:],))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass

    scheduler.add_job(
        _structure_watch,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="8,23,38,53",
                    timezone=et),
        id="structure_watch_market", replace_existing=True,
    )

    # Structure screen — 11:05 PM ET, after the nightly price settle,
    # so the shelves read from settled closes.
    def _structure_screen_job():
        from analysis.structure_screen import run_structure_screen
        run_structure_screen()

    scheduler.add_job(
        _structure_screen_job,
        CronTrigger(day_of_week="mon-fri", hour="23", minute="5", timezone=et),
        id="structure_screen_nightly", replace_existing=True,
    )

    # FINRA short volume + short interest — 6:20 PM ET, Mon-Fri
    scheduler.add_job(
        run_short_side_job,
        CronTrigger(day_of_week="mon-fri", hour="18", minute="20", timezone=et),
        id="short_side_daily",
        replace_existing=True,
    )

    # VIX/VIX3M settle — 4:40 PM ET, Mon-Fri
    scheduler.add_job(
        run_vix_job,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="40", timezone=et),
        id="vix_daily",
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

    # Daily chart-pattern scan — 6:45 AM ET, Mon-Fri (after screens + gems).
    scheduler.add_job(
        run_daily_pattern_scan,
        CronTrigger(day_of_week="mon-fri", hour="6", minute="45", timezone=et),
        id="daily_pattern_scan",
        replace_existing=True,
    )

    # 4h pattern refresh — 12:45 PM ET, Mon-Fri.
    scheduler.add_job(
        run_midday_pattern_scan,
        CronTrigger(day_of_week="mon-fri", hour="12", minute="45", timezone=et),
        id="midday_pattern_scan",
        replace_existing=True,
    )

    # Hourly 4h/1h oscillator refresh through the session — the intraday
    # screens read stored rows, so the rows must track the tape, not the
    # morning. (12:45's pattern chain covers the noon hour.)
    scheduler.add_job(
        run_hourly_oscillator_refresh,
        CronTrigger(day_of_week="mon-fri", hour="10,11,13,14,15", minute="5",
                    timezone=et),
        id="hourly_oscillator_refresh",
        replace_existing=True,
    )

    # Same-evening close sync + full re-stamp — 4:35 PM ET: the session's
    # daily bars from Polygon (grouped daily, one call), then daily/weekly
    # oscillator rows carry TODAY at evening review time.
    scheduler.add_job(
        run_close_sync_and_restamp,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="35", timezone=et),
        id="close_sync_restamp",
        replace_existing=True,
    )

    # Settling re-stamp after the official nightly ingest (10 PM ET), with
    # a second slot for late ingests.
    scheduler.add_job(
        run_evening_oscillator_restamp,
        CronTrigger(day_of_week="mon-fri", hour="22", minute="45", timezone=et),
        id="evening_oscillator_restamp",
        replace_existing=True,
    )
    scheduler.add_job(
        run_evening_oscillator_restamp,
        CronTrigger(day_of_week="mon-fri", hour="23", minute="30", timezone=et),
        id="evening_oscillator_restamp_late",
        replace_existing=True,
    )

    # Nightly ATM-IV snapshot — 5:35 PM ET, Mon-Fri (after close; options
    # snapshots settled). Builds the proprietary IV-rank history.
    scheduler.add_job(
        run_iv_snapshot_job,
        CronTrigger(day_of_week="mon-fri", hour="17", minute="35", timezone=et),
        id="iv_snapshot",
        replace_existing=True,
    )

    # Momentum scanners (Phase A): pre-market gappers pass at 8:50 AM ET,
    # then every 10 minutes through the session. One full-market snapshot
    # per pass; freshness is whatever the data plan allows and is labeled
    # on the tab.
    scheduler.add_job(
        run_momentum_scan_job,
        CronTrigger(day_of_week="mon-fri", hour="8", minute="50", timezone=et),
        id="momentum_premarket",
        replace_existing=True,
    )
    scheduler.add_job(
        run_momentum_scan_job,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="35,45,55", timezone=et),
        id="momentum_open",
        replace_existing=True,
    )
    scheduler.add_job(
        run_momentum_scan_job,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute="*/10", timezone=et),
        id="momentum_session",
        replace_existing=True,
    )
    # Nightly gamma levels — 5:50 PM ET, after the IV snapshot: walls,
    # flip, net GEX for indexes + watchlist from the settled chain.
    scheduler.add_job(
        run_gex_job,
        CronTrigger(day_of_week="mon-fri", hour="17", minute="50", timezone=et),
        id="gex_levels",
        replace_existing=True,
    )

    # Nightly market history + float refresh feeding relvol/2W/float.
    scheduler.add_job(
        run_ticker_stats_job,
        CronTrigger(day_of_week="mon-fri", hour="17", minute="15", timezone=et),
        id="ticker_stats_refresh",
        replace_existing=True,
    )

    scheduler.start()

    # One-time seed on a fresh deploy so the Patterns tab isn't empty until
    # the next 6:45 AM slot. Background thread — never blocks startup.
    import threading

    # Swing options-expression shadow (2026-08-27): every swing fill gets
    # the option ticket the desk WOULD buy, priced at entry and at the
    # live trade's exit — the wrapper graded against shares per the
    # ledger-grades-the-signal rule. Measurement only; the live options
    # paper book stays behind the swing book's ~30-resolution gate.
    def _options_expression():
        try:
            from analysis.options_expression import run_options_expression
            run_options_expression()
        except Exception:
            import traceback
            log.exception("[opt-expr] failed")
            try:
                from screen.reversal_screen import _conn
                conn = _conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO ingestion_log
                               (job_name, status, started_at, completed_at,
                                records_processed, errors_count, error_summary)
                               VALUES ('options_expression','error',now(),now(),
                                       0,1,%s)""",
                            (traceback.format_exc()[-900:],))
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass

    scheduler.add_job(
        _options_expression,
        CronTrigger(day_of_week="mon-fri", hour="9", minute="57", timezone=et),
        id="options_expression_open", replace_existing=True,
    )
    scheduler.add_job(
        _options_expression,
        CronTrigger(day_of_week="mon-fri", hour="10-15", minute="12,42",
                    timezone=et),
        id="options_expression_market", replace_existing=True,
    )
    scheduler.add_job(
        _options_expression,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="35", timezone=et),
        id="options_expression_close", replace_existing=True,
    )

    # Walking-target shadow: daily 16:47 pass shadows the day's newly
    # resolved gamma trades from recorded bars + boards (2026-08-28).
    def _target_shadow_daily():
        try:
            from analysis.target_shadow import run_target_shadow
            run_target_shadow()
        except Exception:
            log.exception("[target-shadow] daily pass failed")

    scheduler.add_job(
        _target_shadow_daily,
        CronTrigger(day_of_week="mon-fri", hour="16", minute="47", timezone=et),
        id="target_shadow_daily", replace_existing=True,
    )

    # 16D green-dot screen upkeep (2026-08-29): after the nightly price
    # settle, append dots when a 16D block completed and fill forward
    # outcomes whose history has arrived.
    def _greendot_nightly():
        try:
            from analysis.greendot_screen import (maybe_append_new_dots,
                                                  update_forward_outcomes)
            maybe_append_new_dots()
            update_forward_outcomes()
        except Exception:
            log.exception("[greendot-screen] nightly failed")

    scheduler.add_job(
        _greendot_nightly,
        CronTrigger(day_of_week="mon-fri", hour="23", minute="20", timezone=et),
        id="greendot_nightly", replace_existing=True,
    )

    def _seed_options_catchup():
        """Boot catch-up for the options-expression shadow: a deploy
        restart between cron slots can miss a same-day fill's ticket
        window (2026-08-28: VRTX's re-ticket waited on a cron that had
        already passed). Idempotent — processes only unticketed fills."""
        try:
            from analysis.options_expression import run_options_expression
            run_options_expression()
        except Exception as e:
            log.warning(f"[scheduler] options catch-up skipped: {e}")

    def _seed_mega_replay_if_missing():
        """One-shot: the index gamma playbook graded on the mega-cap
        boards (2026-08-28) — same build_gamma_specs/simulate_day code
        path, research-fetched 15m bars, marker-retired."""
        try:
            from analysis.gamma_mega_replay import run
            run()
        except Exception as e:
            log.warning(f"[scheduler] mega replay seed skipped: {e}")

    def _seed_target_shadow():
        """Retro + catch-up: frozen vs walking targets for every
        resolved gamma trade, from recorded bars + recorded boards."""
        try:
            from analysis.target_shadow import run_target_shadow
            run_target_shadow()
        except Exception as e:
            log.warning(f"[scheduler] target shadow seed skipped: {e}")

    def _seed_greendot_study():
        """The 16D below-zero green-dot study (VFF archetype) — chunked
        fleet passes with resume; loops until this boot's budget is
        spent or the fleet completes."""
        try:
            from analysis.greendot_study import run
            for _ in range(12):          # ~4,800 tickers max per boot
                if run():
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot seed skipped: {e}")

    def _seed_greendot_multiscale():
        """The multiscale dot study (2026-08-29): the same below-zero
        dot on daily and weekly bars, fixed daily-horizon outcomes —
        Eric's clock-speed compounding question. Resume per scale."""
        try:
            from analysis.greendot_multiscale import run
            for _ in range(16):
                if run():
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot multiscale seed skipped: {e}")

    def _seed_greendot_stack():
        """The full-stack dot study (2026-08-29 night): Eric's actual
        entry — %R/RSI/MACD turning + location proxy — tagged at every
        recorded dot. Chunked with resume; marker-retired."""
        try:
            from analysis.greendot_stack import run
            for _ in range(16):
                if run():
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot stack seed skipped: {e}")

    def _seed_reddot():
        """The 16D red-dot study (2026-08-29): the green dot's mirror
        — bearish cross-downs above zero, the candidate EXIT rule.
        Chunked fleet pass with resume."""
        try:
            from analysis.reddot_study import run
            for _ in range(16):
                if run():
                    break
        except Exception as e:
            log.warning(f"[scheduler] reddot seed skipped: {e}")

    def _seed_greendot_sweep():
        """The block-size sweep (2026-08-29): the dot at 3/8/12/21/26/32
        day blocks — Eric's open timeframe search. Runs LAST so the
        core passes finish first; exploratory, readout renders the
        whole curve or nothing."""
        try:
            from analysis.greendot_scale_sweep import run
            for _ in range(40):          # the overnight curve: ~10k
                if run(batch=250):       # ticker-scale units per boot
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot sweep seed skipped: {e}")

    def _seed_greendot_gate():
        """The gated-ladder study (2026-08-29): tranches 2/3 arm only
        after a daily 8/21 reclaim — the knife cohort walled off from
        the add money. Variant 'ladder_gated' beside the baseline."""
        try:
            from analysis.greendot_ladder_gate import run, run_v2
            for _ in range(20):
                if run():
                    break
            for _ in range(20):
                if run_v2():
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot gate seed skipped: {e}")

    def _seed_greendot_align():
        """Daily-dot alignment pass (2026-08-29, Eric: the daily trade
        is dot + price above the 8/21 before the EMAs flip). Trails
        the multiscale base pass; finishes as it finishes."""
        try:
            from analysis.greendot_ms_align import run, run_weekly
            for _ in range(16):
                if run():
                    break
            for _ in range(16):
                if run_weekly():
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot align seed skipped: {e}")

    def _seed_greendot_ema():
        """EMA-reclaim entry variants (2026-08-29, Eric: price above
        the 8/21 EMAs after the dot, no cross required) — daily and
        16d-bar readings both graded into greendot_entry."""
        try:
            from analysis.greendot_ema_entry import run, run_weekly
            for _ in range(20):
                if run():
                    break
            for _ in range(20):
                if run_weekly():
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot ema seed skipped: {e}")

    def _seed_greendot15():
        """The 15-day-block robustness pass (2026-08-29, the 3W-chart
        question): same dot spec re-blocked at 15 trading days; the
        edge must survive re-blocking or be trusted less. Research
        one-shot with resume; writes only its own tables."""
        try:
            from analysis.greendot_robust15 import run
            for _ in range(12):
                if run():
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot15 seed skipped: {e}")

    def _seed_greendot_entry():
        """The green-dot ENTRY-SCHEDULE study (2026-08-29, Eric's HA-doji
        refinement): five pre-registered variants per dot, graded on real
        closes. Chunked with resume, marker-retired; writes only
        greendot_entry. Runs after the dot study so dots exist."""
        try:
            from analysis.greendot_entry_study import run
            for _ in range(20):          # ~8,000 tickers max per boot
                if run():
                    break
        except Exception as e:
            log.warning(f"[scheduler] greendot entry seed skipped: {e}")

    def _seed_fill_audit_if_missing():
        """One-shot 1-minute-tape forensics on the 2026-08-27 SPY gamma
        fills (trades 86/87) — verdicts to fill_audit, marker-retired.
        Read-only over the books by the module's own signature."""
        try:
            from analysis.fill_audit import run
            run()
        except Exception as e:
            log.warning(f"[scheduler] fill audit seed skipped: {e}")

    def _seed_all():
        try:
            from analysis.options_picker import entitlement_probe
            entitlement_probe()   # one log line: is options data flowing?
        except Exception:
            pass
        _seed_pattern_scan_if_stale()
        _run_missed_daily_pattern_scan()
        _seed_oscillator_if_empty()
        _seed_momentum_if_empty()
        _seed_iv_snapshot_if_missing()
        _seed_gex_if_missing()
        _seed_vix_if_missing()
        _seed_short_if_missing()
        _seed_oscillator_backtest_if_empty()
        _seed_spec_bars_aug7_if_missing()
        _seed_pattern_backtest_if_empty()
        _seed_cipher_study_if_missing()
        _seed_defense_study_if_missing()
        _seed_defense_retro_if_missing()
        _seed_sector_study_if_missing()
        _seed_daybias_bars_if_missing()
        _seed_structure_screen_if_empty()
        _seed_fill_audit_if_missing()
        _seed_mega_replay_if_missing()
        _seed_target_shadow()
        _seed_options_catchup()
        _seed_greendot_study()
        _seed_greendot_entry()
        _seed_greendot15()
        _seed_greendot_ema()
        _seed_greendot_multiscale()
        _seed_greendot_align()
        _seed_greendot_gate()
        _seed_greendot_stack()
        _seed_reddot()
        _seed_greendot_sweep()

    threading.Thread(target=_seed_all, name="pattern-seed", daemon=True).start()

    log.info(
        "[scheduler] Scheduler started (America/New_York). "
        "Daily screens 6:00 AM → Hidden gems 6:30 AM → Patterns 6:45 AM → "
        "Pre-market 15-min (7:00-9:15 AM) → "
        "Market hours 5-min (9:30 AM-4:00 PM ET, email gated) → "
        "4h pattern refresh 12:45 PM."
    )
    return scheduler
