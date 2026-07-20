"""
Watchtower Short Side — squeeze context from free public data.

The vendor "short squeeze" products reduce to two commodity feeds, so
we use the sources directly:

  daily short volume   FINRA Reg SHO consolidated files (public CDN,
                       published ~6 PM ET): per-ticker short vs total
                       volume for the session. The RATIO's deviation
                       from its own trailing range is the signal-ish
                       part; the level alone is noise (market-making
                       prints ~40-50% "short" on normal days).
  short interest       bi-monthly exchange short interest via FMP,
                       stored on ticker_stats. Divide by float for
                       SI%, by 20-day volume for days-to-cover.

Squeeze score is a bounded 0-100 CONTEXT dial (SI% of float up to 40,
days-to-cover up to 30, today's short-volume percentile up to 30) —
same doctrine as gamma magnitude and VIX zones: a dial, not a trigger.
"""
import logging
import os
import time
from datetime import date, timedelta

log = logging.getLogger(__name__)

_FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
BACKFILL_DAYS = 90
SI_STALE_DAYS = 4          # bi-monthly data; refresh attempts every few days
SI_FETCH_CAP = 150         # per-symbol FMP calls per run, stale-first


def _conn():
    from screen.reversal_screen import _conn as c
    return c()


def _key() -> str:
    return os.environ.get("FMP_API_KEY", "").strip()


def _scrub(e) -> str:
    import re
    return re.sub(r"apikey=[^&\s'\"]+", "apikey=***", str(e))


def _fetch_finra_day(d: date) -> list:
    """[(ticker, date, short_vol, total_vol, ratio)] for one session;
    [] on 404 (holiday/weekend/not yet published)."""
    import requests
    url = _FINRA_URL.format(ymd=d.strftime("%Y%m%d"))
    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    rows = []
    for line in resp.text.splitlines():
        parts = line.split("|")
        # Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
        if len(parts) < 5 or parts[0] in ("Date",) or not parts[1]:
            continue
        try:
            sv, tv = int(float(parts[2])), int(float(parts[4]))
        except (TypeError, ValueError):
            continue
        if tv <= 0:
            continue
        rows.append((parts[1].strip().upper(), d, sv, tv,
                     round(sv / tv, 4)))
    return rows


def run_short_volume_update() -> dict:
    """Pull FINRA daily files into short_volume_daily — the last session
    normally; a BACKFILL_DAYS sweep when the table is sparse."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT max(as_of), count(DISTINCT as_of) "
                        "FROM short_volume_daily")
            last, days = cur.fetchone()
        sparse = (days or 0) < 20
        start = date.today() - timedelta(days=BACKFILL_DAYS if sparse else 5)
        if last and not sparse:
            start = max(start, last + timedelta(days=1))
        stored, sessions = 0, 0
        d = start
        while d <= date.today():
            if d.weekday() < 5:
                try:
                    rows = _fetch_finra_day(d)
                except Exception as e:
                    log.warning(f"[short] FINRA {d} failed: {e}")
                    rows = []
                if rows:
                    with conn.cursor() as cur:
                        cur.executemany("""
                            INSERT INTO short_volume_daily
                                (ticker, as_of, short_vol, total_vol, ratio)
                            VALUES (%s,%s,%s,%s,%s)
                            ON CONFLICT (ticker, as_of) DO UPDATE SET
                                short_vol=EXCLUDED.short_vol,
                                total_vol=EXCLUDED.total_vol,
                                ratio=EXCLUDED.ratio
                        """, rows)
                    conn.commit()
                    stored += len(rows)
                    sessions += 1
                    time.sleep(0.3)
            d += timedelta(days=1)
        log.info(f"[short] stored {stored} rows across {sessions} sessions"
                 f"{' (backfill)' if sparse else ''}")
        return {"rows": stored, "sessions": sessions, "backfill": sparse}
    finally:
        conn.close()


def _fetch_short_interest(ticker: str):
    """Latest short interest (shares) via FMP — stable endpoint first,
    legacy v4 fallback; None when neither serves it."""
    import requests
    last_err = None
    for url in ("https://financialmodelingprep.com/stable/short-interest",
                "https://financialmodelingprep.com/api/v4/short_interest"):
        try:
            resp = requests.get(url, params={"symbol": ticker,
                                             "apikey": _key()}, timeout=10)
            resp.raise_for_status()
            data = resp.json() or []
            row = data[0] if isinstance(data, list) and data else data
            if not row:
                continue
            for k in ("shortInterest", "totalShortInterest", "shares"):
                if row.get(k) is not None:
                    return int(float(row[k]))
        except Exception as e:
            last_err = e
    if last_err:
        log.debug(f"[short] SI {ticker}: {_scrub(last_err)}")
    return None


def run_short_interest_update() -> dict:
    """Refresh ticker_stats.short_interest, stale-first, capped. Names:
    active watchlist plus the biggest screener caps — SI matters most
    where we trade, not the whole market."""
    if not _key():
        return {"error": "no FMP key"}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.ticker FROM (
                    SELECT ticker FROM watchlist WHERE active = true
                    UNION
                    SELECT ticker FROM (
                        SELECT ticker FROM screener_snapshot
                        WHERE market_cap IS NOT NULL
                        ORDER BY market_cap DESC LIMIT 600
                    ) big
                ) t
                LEFT JOIN ticker_stats s ON s.ticker = t.ticker
                WHERE s.si_updated_at IS NULL
                   OR s.si_updated_at < now() - make_interval(days => %s)
                ORDER BY s.si_updated_at NULLS FIRST
                LIMIT %s
            """, (SI_STALE_DAYS, SI_FETCH_CAP))
            todo = [r[0] for r in cur.fetchall()]
        got = []
        for t in todo:
            si = _fetch_short_interest(t)
            time.sleep(0.25)
            if si is not None:
                got.append((t, si))
        if got:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO ticker_stats
                        (ticker, short_interest, si_updated_at, updated_at)
                    VALUES (%s, %s, now(), now())
                    ON CONFLICT (ticker) DO UPDATE SET
                        short_interest = EXCLUDED.short_interest,
                        si_updated_at = now(), updated_at = now()
                """, got)
            conn.commit()
        elif todo:
            # Endpoint may not be on the plan — stamp attempts so the
            # stale-first queue doesn't spin on the same names forever.
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO ticker_stats (ticker, si_updated_at, updated_at)
                    VALUES (%s, now(), now())
                    ON CONFLICT (ticker) DO UPDATE SET
                        si_updated_at = now(), updated_at = now()
                """, [(t,) for t in todo])
            conn.commit()
        log.info(f"[short] SI refreshed {len(got)}/{len(todo)}")
        return {"fetched": len(got), "attempted": len(todo)}
    finally:
        conn.close()


def get_short_context(ticker: str) -> dict:
    """Squeeze dial for one name: today's short-volume ratio vs its own
    trailing range, SI% of float, days-to-cover, bounded 0-100 score."""
    ticker = ticker.upper().strip()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT as_of, ratio FROM short_volume_daily
                WHERE ticker = %s ORDER BY as_of DESC LIMIT 60
            """, (ticker,))
            sv = cur.fetchall()
            cur.execute("""
                SELECT short_interest, float_shares, avg_vol_20d,
                       si_updated_at
                FROM ticker_stats WHERE ticker = %s
            """, (ticker,))
            st = cur.fetchone()
    finally:
        conn.close()
    out = {}
    if sv:
        ratios = [float(r[1]) for r in sv if r[1] is not None]
        today = ratios[0] if ratios else None
        if today is not None and len(ratios) >= 10:
            avg20 = sum(ratios[1:21]) / max(1, len(ratios[1:21]))
            pct = round(100 * sum(1 for r in ratios if r <= today)
                        / len(ratios))
            out.update(as_of=str(sv[0][0]), svr=round(today, 3),
                       svr_avg20=round(avg20, 3), svr_pctile=pct)
    si = float(st[0]) if st and st[0] else None
    flt = float(st[1]) if st and st[1] else None
    adv = float(st[2]) if st and st[2] else None
    if si:
        out["short_interest"] = si
        out["si_as_of"] = str(st[3])[:10] if st[3] else None
        if flt:
            out["si_pct_float"] = round(100 * si / flt, 1)
        if adv:
            out["days_to_cover"] = round(si / adv, 1)
    if out:
        score = 0.0
        if out.get("si_pct_float") is not None:
            score += min(40.0, out["si_pct_float"] / 30.0 * 40.0)
        if out.get("days_to_cover") is not None:
            score += min(30.0, out["days_to_cover"] / 10.0 * 30.0)
        if out.get("svr_pctile") is not None:
            score += out["svr_pctile"] / 100.0 * 30.0
        out["squeeze_score"] = round(score)
        out["squeeze_label"] = ("high" if score >= 60 else
                                "moderate" if score >= 30 else "low")
    return out
