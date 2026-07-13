"""
Watchtower Momentum — the day-trading scanner layer.

One full-market Polygon snapshot per pass feeds the momentum scanners,
each row joined at read time with the column no momentum scanner has:
the name's SWING STRUCTURE (live pattern, trigger distance, oscillator
state).

Scanners (Phase A — runs on delayed data; freshness is labeled, not faked):
  gappers       gap >= 4% vs prior close (both directions listed; the
                8:50 AM pre-market pass is the morning watchlist builder)
  pillars       the Ignition score — the classic small-cap momentum
                recipe, SCORED not just filtered: price $1-20 · day
                change >= +10% · relative volume >= 5x · float <= 20M ·
                news catalyst. 5/5 = textbook; 4/5 ranks below so
                near-misses stay visible.
  continuation  2-week movers (>= 30% off the 10-session-ago close) — the
                multi-day tape, and the bridge to the swing book.
  earnings_gap  reported within the last day AND gapping >= 4%.

Phase B (needs the real-time upgrade): HOD momentum bursts, velocity
alerts (up 5%/5min, 10%/10min), halts. Those are seconds-class signals;
on delayed data they would be history dressed as alerts, so they are
absent until the data supports them.

Data notes:
  - relvol (daily rate) = today's cumulative volume vs the 20-day average
    volume prorated by session elapsed time. Volume history for the WHOLE
    market (not just our stored universe) comes from Polygon's grouped-
    daily endpoint: one call per session, 21 sessions kept in ticker_stats
    (avg_vol_20d, close_2w, last_big_day).
  - float lives in ticker_stats: FMP (bulk, else per-symbol) when the
    plan allows; otherwise Polygon shares outstanding — an upper bound
    on float, so the <=20M gate stays conservative. Fetched for current
    scanner names hottest-first, cached 14 days, topped up every pass.
  - "Momo memory" = the name printed a +25% close-to-close day recently —
    momentum names repeat, and traders trust a ticker that has proven it
    can move.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

GAP_MIN = 4.0            # gapper threshold (%)
PILLAR_PRICE = (1.0, 20.0)
PILLAR_CHG = 10.0        # day change %
PILLAR_RELVOL = 5.0
PILLAR_FLOAT = 20_000_000
CONTINUATION_MIN = 30.0  # 2-week move %
FORMER_MOMO_DAY = 25.0   # single-day close-to-close gain %
MAX_ROWS = 1200          # snapshot table cap per pass
FLOAT_TTL_DAYS = 14      # per-symbol float cache lifetime
FLOAT_FETCH_CAP = 250    # max per-symbol float fetches per pass


def _fmp_key() -> str:
    return os.environ.get("FMP_API_KEY", "").strip()


def refresh_ticker_stats() -> dict:
    """Nightly float refresh from FMP's bulk endpoint into ticker_stats.
    One call, whole market — works only on plans that include the bulk
    endpoint; on plans without it the per-symbol fallback
    (refresh_floats_for) carries the load instead."""
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
        log.warning(f"[momentum] bulk float refresh failed "
                    f"(plan may not include it): {e}")
        return {"error": str(e)[:120]}
    if not rows:
        return {"stored": 0}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO ticker_stats
                    (ticker, float_shares, float_updated_at, updated_at)
                VALUES (%s, %s, now(), now())
                ON CONFLICT (ticker) DO UPDATE SET
                    float_shares = EXCLUDED.float_shares,
                    float_updated_at = now(), updated_at = now()
            """, rows)
        conn.commit()
    finally:
        conn.close()
    log.info(f"[momentum] ticker_stats refreshed: {len(rows)} floats")
    return {"stored": len(rows)}


def refresh_floats_for(tickers: list, cap: int = FLOAT_FETCH_CAP) -> int:
    """Per-symbol float fetch — the fallback when FMP's bulk endpoint
    isn't on the plan. Tries FMP's per-symbol float first (true float);
    the moment that proves unavailable too, switches to Polygon ticker
    details and stores shares outstanding — an upper bound on float, so
    the <=20M pillar stays conservative. Only touches names missing a
    value or older than FLOAT_TTL_DAYS; capped and paced. Caller passes
    tickers hottest-first so the cap spends its budget on the names that
    matter."""
    import requests
    from screen.reversal_screen import _conn
    key = _fmp_key()
    if not tickers:
        return 0
    tickers = [str(t).upper() for t in tickers]
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker FROM ticker_stats
                WHERE ticker = ANY(%s)
                  AND float_shares IS NOT NULL
                  AND float_updated_at > now() - make_interval(days => %s)
            """, (tickers, FLOAT_TTL_DAYS))
            fresh = {r[0] for r in cur.fetchall()}
        todo = [t for t in tickers if t not in fresh][:cap]
        if not todo:
            return 0
        got = []
        use_fmp = bool(key)
        poly = None
        for t in todo:
            flt = None
            if use_fmp:
                try:
                    resp = requests.get(
                        "https://financialmodelingprep.com/api/v4/shares_float",
                        params={"symbol": t, "apikey": key}, timeout=10)
                    resp.raise_for_status()
                    data = resp.json() or []
                    flt = (data[0] or {}).get("floatShares") if data else None
                    time.sleep(0.25)   # ~240 calls/min ceiling
                except Exception as e:
                    log.warning(f"[momentum] FMP per-symbol float failed "
                                f"({e}) — using Polygon shares outstanding")
                    use_fmp = False
            if flt is None and not use_fmp:
                if poly is None:
                    from analysis.polygon_data import get_client
                    poly = get_client()
                    if not poly:
                        break
                try:
                    det = poly.get_ticker_details(t)
                    flt = (getattr(det, "share_class_shares_outstanding", None)
                           or getattr(det, "weighted_shares_outstanding", None))
                    time.sleep(0.05)
                except Exception:
                    continue
            if flt:
                try:
                    got.append((t, int(float(flt))))
                except (TypeError, ValueError):
                    continue
        if got:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO ticker_stats
                        (ticker, float_shares, float_updated_at, updated_at)
                    VALUES (%s, %s, now(), now())
                    ON CONFLICT (ticker) DO UPDATE SET
                        float_shares = EXCLUDED.float_shares,
                        float_updated_at = now(), updated_at = now()
                """, got)
            conn.commit()
        log.info(f"[momentum] per-symbol floats: {len(got)}/{len(todo)} "
                 f"fetched ({len(fresh)} cached fresh)")
        return len(got)
    finally:
        conn.close()


def refresh_market_history(days: int = 21) -> dict:
    """Whole-market volume/close history from Polygon's grouped-daily
    endpoint (one call per session, every listed name in the response).
    Fills ticker_stats.avg_vol_20d / close_2w / last_big_day so relvol,
    2-week move and momo memory work for the ENTIRE tape, not just the
    names our swing universe happens to store in daily_prices.

    last_big_day only moves forward (GREATEST on conflict), so momo
    memory accumulates across nightly runs instead of being limited to
    the ~21-session window each run can see."""
    from analysis.polygon_data import get_client
    from screen.reversal_screen import _conn
    client = get_client()
    if not client:
        return {"error": "no polygon client"}

    t0 = time.time()
    sessions = []   # newest-first: (date, {ticker: (close, volume)})
    d = datetime.now(timezone.utc).date()
    probes = 0
    while len(sessions) < days and probes < days * 2 + 10:
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        probes += 1
        try:
            bars = client.get_grouped_daily_aggs(d.isoformat(), adjusted=True)
        except Exception as e:
            log.warning(f"[momentum] grouped daily {d} failed: {e}")
            continue
        day = {}
        for b in bars or []:
            try:
                tkr = str(b.ticker).upper()
                c = float(b.close or 0)
                v = float(b.volume or 0)
                if c > 0:
                    day[tkr] = (c, v)
            except Exception:
                continue
        if day:
            sessions.append((d, day))
    if not sessions:
        return {"error": "no grouped-daily sessions returned"}

    # Per ticker: newest-first closes/vols across the sessions we pulled.
    tickers = set()
    for _, day in sessions:
        tickers.update(day)
    rows = []
    for t in tickers:
        vols, closes = [], []   # aligned to sessions (newest first)
        for _, day in sessions:
            c, v = day.get(t, (None, None))
            closes.append(c)
            vols.append(v)
        known_vols = [v for v in vols[:20] if v is not None]
        av20 = sum(known_vols) / len(known_vols) if known_vols else None
        c2w = closes[10] if len(closes) > 10 else None
        big = None   # latest date with a close-to-close day >= threshold
        for i in range(len(sessions) - 1):
            c_now, c_prev = closes[i], closes[i + 1]
            if c_now and c_prev and (c_now / c_prev - 1) * 100 >= FORMER_MOMO_DAY:
                big = sessions[i][0]
                break
        if av20 is None and c2w is None and big is None:
            continue
        rows.append((t, av20, c2w, big))

    conn = _conn()
    try:
        with conn.cursor() as cur:
            for i in range(0, len(rows), 1000):
                cur.executemany("""
                    INSERT INTO ticker_stats
                        (ticker, avg_vol_20d, close_2w, last_big_day, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (ticker) DO UPDATE SET
                        avg_vol_20d = EXCLUDED.avg_vol_20d,
                        close_2w = EXCLUDED.close_2w,
                        last_big_day = GREATEST(ticker_stats.last_big_day,
                                                EXCLUDED.last_big_day),
                        updated_at = now()
                """, rows[i:i + 1000])
        conn.commit()
    finally:
        conn.close()
    log.info(f"[momentum] market history: {len(sessions)} sessions, "
             f"{len(rows)} tickers in {time.time()-t0:.1f}s")
    return {"sessions": len(sessions), "tickers": len(rows)}


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
            cur.execute("""
                SELECT ticker, float_shares, short_interest,
                       avg_vol_20d, close_2w, last_big_day
                FROM ticker_stats WHERE ticker = ANY(%s)
            """, (tickers,))
            stats = {r[0]: r[1:] for r in cur.fetchall()}
            cur.execute("""
                SELECT DISTINCT ticker FROM earnings_calendar
                WHERE report_date BETWEEN CURRENT_DATE - 1 AND CURRENT_DATE
            """)
            reported = {r[0] for r in cur.fetchall()}

        momo_floor = (datetime.now(timezone.utc) - timedelta(days=370)).date()
        rows = []
        for t, c in cands.items():
            av20, c2w, mx = hist.get(t, (None, None, None))
            flt, si, ts_av20, ts_c2w, big_day = \
                stats.get(t, (None, None, None, None, None))
            # daily_prices history (deeper) wins; grouped-daily market
            # history in ticker_stats covers everything else on the tape.
            if av20 is None and ts_av20:
                av20 = float(ts_av20)
            if c2w is None and ts_c2w:
                c2w = float(ts_c2w)
            momo = bool(mx and mx >= FORMER_MOMO_DAY) or \
                bool(big_day and big_day >= momo_floor)
            relvol = None
            if av20 and av20 > 0 and frac > 0:
                relvol = round(c["vol"] / (av20 * frac), 2)
            move2w = (round((c["price"] / c2w - 1) * 100, 1)
                      if c2w and c2w > 0 else None)
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
                momo, t in reported, session))
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

    # Float top-up: rows are already sorted hottest-first, so the fetch
    # cap goes to the biggest movers still missing a float.
    try:
        missing = [r[0] for r in rows if r[6] is None]
        if missing:
            refresh_floats_for(missing)
    except Exception as e:
        log.warning(f"[momentum] float top-up failed: {e}")
    return {"rows": len(rows), "session": session}
