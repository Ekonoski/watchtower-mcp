"""
🩺 The morning health census (2026-09-08, the first trading day after
Labor Day — Eric: "make sure everything is running as it should this
morning… we had a long holiday weekend" / "It's why I audit it pretty
much every morning"). This is that audit as one call: every feed's
freshness in ITS OWN units, today's specs by book, the pings expected by
now and which posted, the overnight ingestion jobs with their error
counts, the last deploy. Nothing summarizes into a checkmark — a stale
feed prints its own date, a missing one prints as a hole, an undelivered
ping is named. Read-only over every table; the 9:55 post to #desk is
at-most-once per day via the discord_notify_log claim.
"""
import datetime as dt
import logging
from zoneinfo import ZoneInfo

log = logging.getLogger("watchtower.health")

ET = ZoneInfo("America/New_York")
CHANNEL = "desk"
KIND_HEALTH = "health"

# pings expected on a trading day, with the ET time each is due
EXPECTED_PINGS = (
    ("gamma_board", "🌅 gamma board", dt.time(8, 5)),
    ("flipprox", "🧭 flip proximity", dt.time(9, 31)),
    ("rsl_rank", "🏁 leader rank", dt.time(9, 45)),
    ("day_bias", "📐 day-bias verdict", dt.time(9, 51)),
)
# the usual overnight "errors": names with no such data (preferreds, warrants, CEFs)
USUAL_FMP_MISSES = {"fmp_news_sentiment": 40, "fmp_analyst_estimates": 120,
                    "fmp_financial_scores": 40, "fmp_fundamentals_refresh": 20}


def _one(c, sql, *args):
    c.execute(sql, args)
    r = c.fetchone()
    return r[0] if r else None


def prior_session(conn, today):
    """The last trading date before today per SPY's own daily record."""
    with conn.cursor() as c:
        return _one(c, "SELECT max(trade_date) FROM daily_prices WHERE ticker='SPY' AND trade_date < %s", today)


def freshness_lines(conn, now):
    """Every feed, its own stamp. Pure over the connection."""
    today = now.date()
    prev = prior_session(conn, today)
    out = []
    with conn.cursor() as c:
        n_prev = _one(c, "SELECT count(*) FROM daily_prices WHERE trade_date=%s", prev) if prev else 0
        n_prev2 = _one(c, "SELECT count(*) FROM daily_prices WHERE trade_date=(SELECT max(trade_date) FROM daily_prices WHERE ticker='SPY' AND trade_date < %s)", prev) if prev else 0
        out.append(f"daily bars: through {prev} for {n_prev:,} tickers (session before: {n_prev2:,})"
                   + ("" if n_prev >= 0.95 * max(n_prev2, 1) else "  ⚠ short — nightly price job incomplete?"))
        for tbl, nm in (("index_intraday_bars", "index 15m"), ("mag7_1m_bars", "mag-7 1m"),
                        ("liquid_1m_bars", "liquid 1m"), ("rsl_book_bars", "RS-leader book 1m")):
            ts = _one(c, f"SELECT max(ts) AT TIME ZONE 'America/New_York' FROM {tbl}")
            out.append(f"{nm}: through {ts:%a %m-%d %H:%M} ET" if ts else f"{nm}: *no rows* (hole)")
        pm_today = _one(c, "SELECT count(*) FROM premarket_range WHERE trade_date=%s", today)
        pm_max = _one(c, "SELECT max(trade_date) FROM premarket_range")
        out.append(f"premarket range: {pm_today} names today (newest day {pm_max})"
                   + ("  — due 9:41" if now.time() < dt.time(9, 42) and pm_today == 0 else "")
                   + ("  ⚠ missing after 9:41" if now.time() >= dt.time(9, 42) and pm_today == 0 else ""))
        c.execute("""SELECT ticker, to_char(max(computed_at) AT TIME ZONE 'America/New_York', 'HH24:MI')
                     FROM gex_levels WHERE ticker IN ('SPY','QQQ','IWM') AND computed_at::date = %s GROUP BY ticker ORDER BY ticker""", (today,))
        g = c.fetchall()
        out.append("gamma marks today: " + (" ".join(f"{t} {h}" for t, h in g) if g else "*none* (hole — the 7:30 sweep did not write)"))
        gi = _one(c, "SELECT max(ts) AT TIME ZONE 'America/New_York' FROM gex_intraday")
        out.append(f"gamma intraday re-price: last {gi:%a %m-%d %H:%M} ET" if gi else "gamma intraday: *no rows*")
        for tf in ("daily", "weekly", "4h", "1h"):
            c.execute("SELECT max(bar_ts), max(scanned_at) AT TIME ZONE 'America/New_York' FROM oscillator_scan WHERE timeframe=%s", (tf,))
            bar, sc = c.fetchone()
            out.append(f"oscillator {tf}: bar {bar:%m-%d %H:%M} scanned {sc:%m-%d %H:%M}" if bar else f"oscillator {tf}: *no rows*")
        fv = _one(c, "SELECT max(computed_at) AT TIME ZONE 'America/New_York' FROM fvg_runs")
        out.append(f"FVG sweep: {fv:%m-%d %H:%M} ET" if fv else "FVG sweep: *no rows*")
        sr = _one(c, "SELECT max(trade_date) FROM sector_rs_daily")
        out.append(f"sector RS: through {sr}" + ("" if sr == prev else f"  ⚠ expected {prev}"))
    return out


def specs_lines(conn, today):
    with conn.cursor() as c:
        c.execute("SELECT book, status, count(*) FROM paper_specs WHERE trade_date=%s GROUP BY 1,2 ORDER BY 1,2", (today,))
        rows = c.fetchall()
    if not rows:
        return ["specs today: *none yet* (swing/gamma write premarket; rs_leader 9:45; day_bias 9:51)"]
    by = {}
    for b, s, n in rows:
        by.setdefault(b, []).append(f"{s} {n}")
    return ["specs today: " + " · ".join(f"{b}: {', '.join(v)}" for b, v in by.items())]


def pings_lines(conn, now):
    today = now.date()
    with conn.cursor() as c:
        c.execute("""SELECT kind, count(*), count(*) FILTER (WHERE NOT delivered),
                            to_char(min(created_at) AT TIME ZONE 'America/New_York', 'HH24:MI')
                     FROM discord_notify_log WHERE created_at >= (%s::timestamp AT TIME ZONE 'America/New_York')
                     GROUP BY kind ORDER BY min(created_at)""", (today,))
        got = {k: (n, nd, t) for k, n, nd, t in c.fetchall()}
    out = []
    for kind, nm, due in EXPECTED_PINGS:
        hit = [k for k in got if k == kind or k.startswith(kind)]
        if hit:
            n = sum(got[k][0] for k in hit)
            nd = sum(got[k][1] for k in hit)
            out.append(f"{nm}: posted {got[hit[0]][2]}" + (f"  ⚠ {nd} undelivered" if nd else ""))
        elif now.time() < due:
            out.append(f"{nm}: due {due:%H:%M}")
        else:
            out.append(f"{nm}: ⚠ NOT posted (due {due:%H:%M})")
    others = [k for k in got if not any(k == kind or k.startswith(kind) for kind, _, _ in EXPECTED_PINGS)]
    if others:
        out.append("other pings today: " + " ".join(f"{k}={got[k][0]}" + (f"(⚠{got[k][1]} undelivered)" if got[k][1] else "") for k in others))
    return out


def ingestion_lines(conn, now):
    since = now - dt.timedelta(hours=20)
    with conn.cursor() as c:
        c.execute("""SELECT job_name, status, to_char(completed_at AT TIME ZONE 'America/New_York', 'HH24:MI'),
                            records_processed, errors_count
                     FROM ingestion_log WHERE started_at >= %s ORDER BY started_at""", (since,))
        rows = c.fetchall()
    if not rows:
        return ["overnight ingestion: *no jobs logged in 20h* (hole — the nightly chain did not run?)"]
    out = []
    for job, st, done, n, err in rows:
        flag = ""
        if st != "completed":
            usual = USUAL_FMP_MISSES.get(job)
            flag = "  (usual no-data names)" if usual and (err or 0) <= usual else "  ⚠ more errors than usual"
        out.append(f"{job}: {st} {done or '?'} n={n if n is not None else '?'} err={err if err is not None else '?'}{flag}")
    return out


def deploy_line():
    import os
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7]
    dep = os.environ.get("RAILWAY_DEPLOYMENT_ID", "")[:8]
    return f"running commit {sha or '?'} deployment {dep or '?'}"


def render_health(conn, now=None) -> str:
    now = now or dt.datetime.now(ET)
    today = now.date()
    lines = [f"🩺 **Morning health — {today:%a %b %-d} {now:%H:%M} ET** ({deploy_line()})"]
    for title, fn in (("__Feeds__", lambda: freshness_lines(conn, now)),
                      ("__Specs__", lambda: specs_lines(conn, today)),
                      ("__Pings__", lambda: pings_lines(conn, now)),
                      ("__Overnight ingestion__", lambda: ingestion_lines(conn, now))):
        lines.append(title)
        try:
            lines += fn()
        except Exception as e:
            conn.rollback()
            lines.append(f"*unavailable* — {type(e).__name__}: {str(e)[:300]}")
    lines.append("_Every line carries its own stamp; a hole is a hole, never a zero._")
    return "\n".join(lines)


def health_report() -> str:
    """MCP: the census on demand."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        return render_health(conn)
    finally:
        conn.close()


def run_health_ping() -> str:
    """9:55 ET: post the census to #desk, at most once per day."""
    from alerts.discord_notify import claim_and_send
    from screen.reversal_screen import _conn
    now = dt.datetime.now(ET)
    if now.weekday() >= 5:
        return "weekend"
    conn = _conn()
    try:
        text = render_health(conn, now)
        return claim_and_send(KIND_HEALTH, now.date().isoformat(), CHANNEL, text, conn=conn)
    finally:
        conn.close()
