"""
Watchtower Momentum — the day-trading scanner layer (Warrior Trading-class).

One full-market Polygon snapshot per pass feeds a set of momentum scanners
built on Ross Cameron's published criteria, with the column Watchtower adds
that no momentum scanner has: the name's SWING STRUCTURE (live pattern,
trigger distance, oscillator state) joined in at read time.

Scanners (Phase A — runs on delayed data; freshness is labeled, not faked):
  gappers       gap >= 4% vs prior close (both directions listed; the
                8:50 AM pre-market pass is the Gap & Go watchlist builder)
  pillars       Ross's 5 Pillars composite, SCORED not just filtered:
                price $1-20 · day change >= +10% · relative volume >= 5x
                · float <= 20M · news catalyst. 5/5 = textbook; 4/5 rows
                rank below so near-misses are visible.
  continuation  2-week movers (>= 30% off the 10-session-ago close) — the
                multi-day tape, and the bridge to the swing book.
  earnings_gap  reported within the last day AND gapping >= 4%.

Phase B (needs the real-time upgrade): HOD momentum bursts, Running Up/Down
velocity alerts, squeeze events (up 5%/5min, 10%/10min), halts. Those are
seconds-class signals; on 15-minute-delayed data they would be history
dressed as alerts, so they are absent until the data supports them.

Data notes:
  - relvol (daily rate) = today's cumulative volume vs the 20-day average
    volume prorated by session elapsed time — the time-of-day-adjusted
    metric Warrior displays, computable from daily history.
  - float / short interest live in ticker_stats, refreshed nightly from
    FMP's bulk shares-float endpoint (short interest best-effort).
  - "Former Momo" = the name printed a +25% close-to-close day inside the
    trailing year — momentum names repeat, and day traders trust a ticker
    that has proven it can move.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

GAP_MIN = 4.0            # Warrior gapper threshold (%)
PILLAR_PRICE = (1.0, 20.0)
PILLAR_CHG = 10.0        # day change %
PILLAR_RELVOL = 5.0
PILLAR_FLOAT = 20_000_000
CONTINUATION_MIN = 30.0  # 2-week move %
FORMER_MOMO_DAY = 25.0   # single-day close-to-close gain %
MAX_ROWS = 1200          # snapshot table cap per pass


def _fmp_key() -> str:
    return os.environ.get("FMP_API_KEY", "").strip()


def refresh_ticker_stats() -> dict:
    """Nightly float (and best-effort short interest) refresh from FMP's
    bulk endpoint into ticker_stats. One call, whole market."""
    import requests
    from screen.reversal_screen import _conn
    key = _fmp_key()
    if not key:
        return {"error": "no FMP key"}
    rows = []
    try:
        resp = requests.get(
            "https://financialmodelingprep.com/api/v4/shares_float/all",
            params={"apikey": key}, timeout=120)
        resp.raise_for_status()
        for r in resp.json() or []:
            sym = (r.get("symbol") or "").strip().upper()
            flt = r.get("floatShares")
            if sym and flt:
                try:
                    rows.append((sym, int(float(flt))))
                except (TypeError, ValueError):
                    continue
    except Exception as e:
        log.warning(f"[momentum] float refresh failed: {e}")
        return {"error": str(e)[:120]}
    if not rows:
        return {"stored": 0}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO ticker_stats (ticker, float_shares, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (ticker) DO UPDATE SET
                    float_shares = EXCLUDED.float_shares, updated_at = now()
            """, rows)
        conn.commit()
    finally:
        conn.close()
    log.info(f"[momentum] ticker_stats refreshed: {len(rows)} floats")
    return {"stored": len(rows)}


def _session_now():
    """('premarket'|'regular'|'closed', fraction of regular session elapsed)."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    mins = now.hour * 60 + now.minute
    if mins < 4 * 60 or now.weekday() >= 5:
        return "closed", 0.0
    if mins < 9 * 60 + 30:
        return "premarket", 0.05
    if mins >= 16 * 60:
        return "closed", 1.0
    return "regular", max((mins - (9 * 60 + 30)) / 390.0, 0.05)


def _news_map(lookback_minutes: int = 990) -> dict:
    """{ticker: headline} for names with a story since ~yesterday's close."""
    try:
        from analysis.news_scanner import fetch_recent_news
        out = {}
        for a in fetch_recent_news(lookback_minutes=lookback_minutes) or []:
            head = (a.get("headline") or a.get("title") or "").strip()
            for t in a.get("tickers") or []:
                out.setdefault(str(t).upper(), head)
        return out
    except Exception as e:
        log.warning(f"[momentum] news fetch failed: {e}")
        return {}


def run_momentum_scan() -> dict:
    """One pass: full-market snapshot -> candidates -> enrich (relvol,
    2-week move, former-momo, float, news, earnings) -> replace
    momentum_scan wholesale."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    client = get_client()
    if not client:
        return {"error": "no polygon client"}
    session, frac = _session_now()

    t0 = time.time()
    try:
        snaps = list(client.get_snapshot_all("stocks", include_otc=False))
    except Exception as e:
        log.error(f"[momentum] snapshot failed: {e}")
        return {"error": str(e)[:120]}

    cands = {}
    for s in snaps:
        try:
            tkr = s.ticker
            day = getattr(s, "day", None)
            prev = getattr(s, "prev_day", None)
            mn = getattr(s, "min", None)
            prev_c = float(getattr(prev, "close", 0) or 0)
            if prev_c <= 0:
                continue
            last = float(getattr(day, "close", 0) or 0) \
                or float(getattr(mn, "close", 0) or 0) or prev_c
            vol = float(getattr(day, "volume", 0) or 0)
            open_ = float(getattr(day, "open", 0) or 0)
            chg = (last / prev_c - 1) * 100
            gap = ((open_ or last) / prev_c - 1) * 100
            if last < 0.5 or (vol < 50_000 and session != "premarket"):
                continue
            if abs(chg) < 3 and abs(gap) < GAP_MIN:
                continue
            cands[tkr.upper()] = {
                "price": round(last, 4), "chg": round(chg, 2),
                "gap": round(gap, 2), "vol": int(vol)}
        except Exception:
            continue
    log.info(f"[momentum] snapshot {len(snaps)} names -> "
             f"{len(cands)} candidates ({session})")
    if not cands:
        return {"rows": 0, "session": session}

    news = _news_map()
    conn = _conn()
    try:
        tickers = sorted(cands)
        hist: dict = {}
        with conn.cursor() as cur:
            for i in range(0, len(tickers), 500):
                batch = tickers[i:i + 500]
                cur.execute("""
                    WITH d AS (
                        SELECT ticker, close, volume,
                               lag(close) OVER (PARTITION BY ticker
                                                ORDER BY trade_date) AS prev_close,
                               row_number() OVER (PARTITION BY ticker
                                                  ORDER BY trade_date DESC) AS rn
                        FROM daily_prices
                        WHERE ticker = ANY(%s)
                          AND trade_date >= CURRENT_DATE - 370
                    )
                    SELECT ticker,
                           avg(volume) FILTER (WHERE rn <= 20)  AS av20,
                           max(close)  FILTER (WHERE rn = 11)   AS c_2w,
                           max(CASE WHEN prev_close > 0
                                    THEN (close / prev_close - 1) * 100
                               END) AS max_day
                    FROM d GROUP BY ticker
                """, (batch,))
                for t, av20, c2w, mx in cur.fetchall():
                    hist[t] = (float(av20) if av20 else None,
                               float(c2w) if c2w else None,
                               float(mx) if mx is not None else None)
            cur.execute("SELECT ticker, float_shares, short_interest "
                        "FROM ticker_stats WHERE ticker = ANY(%s)", (tickers,))
            stats = {t: (f, si) for t, f, si in cur.fetchall()}
            cur.execute("""
                SELECT DISTINCT ticker FROM earnings_calendar
                WHERE report_date BETWEEN CURRENT_DATE - 1 AND CURRENT_DATE
            """)
            reported = {r[0] for r in cur.fetchall()}

        rows = []
        for t, c in cands.items():
            av20, c2w, mx = hist.get(t, (None, None, None))
            relvol = None
            if av20 and av20 > 0 and frac > 0:
                relvol = round(c["vol"] / (av20 * frac), 2)
            move2w = (round((c["price"] / c2w - 1) * 100, 1)
                      if c2w and c2w > 0 else None)
            flt, si = stats.get(t, (None, None))
            headline = news.get(t)
            pillars = {
                "price": PILLAR_PRICE[0] <= c["price"] <= PILLAR_PRICE[1],
                "change": c["chg"] >= PILLAR_CHG,
                "relvol": bool(relvol and relvol >= PILLAR_RELVOL),
                "float": bool(flt and flt <= PILLAR_FLOAT),
                "news": bool(headline),
            }
            rows.append((
                t, c["price"], c["chg"], c["gap"], c["vol"], relvol,
                flt, si, bool(headline), (headline or "")[:200],
                json.dumps(pillars), sum(pillars.values()), move2w,
                bool(mx and mx >= FORMER_MOMO_DAY), t in reported, session))
        rows.sort(key=lambda r: abs(r[2] or 0), reverse=True)
        rows = rows[:MAX_ROWS]

        run_started = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO momentum_scan
                    (ticker, price, day_change_pct, gap_pct, volume, relvol,
                     float_shares, short_interest, news_flag, headline,
                     pillars, pillar_count, move_2w_pct, former_momo,
                     earnings_gap, session, scanned_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,
                        clock_timestamp())
                ON CONFLICT (ticker) DO UPDATE SET
                    price=EXCLUDED.price, day_change_pct=EXCLUDED.day_change_pct,
                    gap_pct=EXCLUDED.gap_pct, volume=EXCLUDED.volume,
                    relvol=EXCLUDED.relvol, float_shares=EXCLUDED.float_shares,
                    short_interest=EXCLUDED.short_interest,
                    news_flag=EXCLUDED.news_flag, headline=EXCLUDED.headline,
                    pillars=EXCLUDED.pillars, pillar_count=EXCLUDED.pillar_count,
                    move_2w_pct=EXCLUDED.move_2w_pct,
                    former_momo=EXCLUDED.former_momo,
                    earnings_gap=EXCLUDED.earnings_gap,
                    session=EXCLUDED.session, scanned_at=clock_timestamp()
            """, rows)
            cur.execute("DELETE FROM momentum_scan WHERE scanned_at < %s",
                        (run_started,))
        conn.commit()
    finally:
        conn.close()
    log.info(f"[momentum] stored {len(rows)} rows in {time.time()-t0:.1f}s")
    return {"rows": len(rows), "session": session}
