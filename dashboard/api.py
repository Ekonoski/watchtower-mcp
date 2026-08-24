"""
Dashboard HTTP API — rides on the same FastMCP/Starlette app as the MCP server.

Routes (all JSON unless noted):
  GET  /dashboard                → the dashboard UI (HTML)
  POST /api/login                → {password} → sets session cookie
  GET  /api/scan/latest          → latest persisted scan snapshot
  POST /api/scan/run             → trigger a full scan now (async, returns immediately)
  GET  /api/ticker/{ticker}      → live single-ticker intraday check + social buzz
  GET  /api/performance          → alert performance report (?days=90&type=intraday)
  GET  /api/swing/latest         → latest swing-screen signals (reversal/momentum/breakdown/insider/gems)
  GET  /api/gems/latest          → hidden-gem candidates + live sector heat map
  GET  /api/heatmap?tf=quarterly  → sector heat map for a timeframe (daily|weekly|monthly|quarterly)
  GET  /api/watchlist            → active watchlist rows
  POST /api/watchlist            → {ticker, notes} → add/re-activate
  DELETE /api/watchlist/{ticker} → deactivate

Auth: single-user password from DASHBOARD_PASSWORD (falls back to
MCP_AUTH_TOKEN). The session cookie is an HMAC derived from the password,
so restarting the server doesn't log you out.
"""
import asyncio
import hashlib
import hmac
import logging
import os
import threading
import time

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

log = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_COOKIE_NAME = "wt_session"
_scan_running = threading.Event()


def _dashboard_password() -> str:
    return (os.environ.get("DASHBOARD_PASSWORD") or os.environ.get("MCP_AUTH_TOKEN") or "").strip()


_SESSION_TTL_SEC = 60 * 60 * 24 * 30  # 30 days, matching the cookie max_age


def _hash_password(password: str) -> str:
    """Salted scrypt (stdlib-only): 'scrypt$n$r$p$salt_hex$hash_hex'."""
    import secrets as _secrets
    salt = _secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                           n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(h.hex(), hash_hex)
    except Exception:
        return False


def _get_user(username: str):
    """Active user row or None. Any DB problem reads as 'no such user' so the
    legacy single-password fallback still lets you in during a bad deploy."""
    from screen.reversal_screen import _conn
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT username, password_hash, role, display_name "
                    "FROM dashboard_users WHERE username = %s AND active = true",
                    (username,))
                r = cur.fetchone()
            if not r:
                return None
            return {"username": r[0], "password_hash": r[1], "role": r[2],
                    "display_name": r[3] or r[0]}
        finally:
            conn.close()
    except Exception:
        return None


def _session_token(username: str, expires_at: int = None) -> str:
    """Signed session token carrying WHO you are: 'v3|<user>|<expiry>.<hmac>'.
    Signed with the server secret (DASHBOARD_PASSWORD env), which is now a
    signing key rather than a login credential — per-user passwords live in
    dashboard_users as scrypt hashes."""
    key = _dashboard_password()
    if not key:
        return ""
    if expires_at is None:
        expires_at = int(time.time()) + _SESSION_TTL_SEC
    payload = f"v3|{username}|{expires_at}"
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _current_user(request: Request):
    """Username from a valid, unexpired v3 cookie — else None. Older cookie
    formats are rejected (a one-time re-login after this deploys)."""
    key = _dashboard_password()
    if not key:
        return "eric"  # no secret configured — open dev mode, act as owner
    cookie = request.cookies.get(_COOKIE_NAME, "")
    if not cookie.startswith("v3|") or "." not in cookie:
        return None
    payload, _, _sig = cookie.rpartition(".")
    parts = payload.split("|")
    if len(parts) != 3:
        return None
    _, username, ts_s = parts
    try:
        expires_at = int(ts_s)
    except ValueError:
        return None
    if time.time() > expires_at:
        return None
    if not hmac.compare_digest(cookie, _session_token(username, expires_at)):
        return None
    return username


def _is_authed(request: Request) -> bool:
    return _current_user(request) is not None


# Login backoff: the password was brute-forceable at network speed. After 5
# failures per client IP, lockouts double from 30s (capped at 1h); success
# clears. In-memory — resets on deploy, which is fine for a one-user dashboard.
_login_fails: dict = {}  # ip -> [fail_count, locked_until_epoch]


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return getattr(getattr(request, "client", None), "host", "") or "unknown"


def _login_throttled(ip: str) -> int:
    """Seconds the caller must still wait, or 0 if allowed."""
    entry = _login_fails.get(ip)
    if not entry:
        return 0
    remaining = int(entry[1] - time.time())
    return max(0, remaining)


def _login_failed(ip: str):
    fails, _ = _login_fails.get(ip, [0, 0])
    fails += 1
    lock = 0
    if fails >= 5:
        lock = time.time() + min(3600, 30 * (2 ** (fails - 5)))
    _login_fails[ip] = [fails, lock]


def _login_succeeded(ip: str):
    _login_fails.pop(ip, None)


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def _swing_rows() -> dict:
    """Assemble the latest swing-screen signals for the Swings tab.

    Four sleeves (reversal/momentum/breakdown/insider) come from alert_log's
    most recent screen date; hidden gems come from up_and_comers_cache. Purely a
    read over what the 6 AM daily jobs already persist — no live screen run.
    """
    from screen.reversal_screen import _conn
    conn = _conn()
    rows, as_of = [], None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, alert_type, score, entry_price, alert_date, signal_details
                FROM alert_log
                WHERE alert_type IN ('reversal','momentum','breakdown','insider')
                  AND alert_date = (
                      SELECT max(alert_date) FROM alert_log
                      WHERE alert_type IN ('reversal','momentum','breakdown','insider')
                  )
                ORDER BY score DESC NULLS LAST
                """
            )
            for tk, sleeve, score, entry, adate, det in cur.fetchall():
                det = det or {}
                d = str(adate) if adate else None
                if d and (as_of is None or d > as_of):
                    as_of = d
                price = det.get("current_price")
                if price is None and entry is not None:
                    price = float(entry)
                rows.append({
                    "ticker": tk, "sleeve": sleeve,
                    "score": float(score) if score is not None else det.get("score"),
                    "company_name": det.get("company_name") or "",
                    "current_price": price,
                    "sector": det.get("sector") or "",
                    "rationale": det.get("rationale") or det.get("signal") or "",
                    "as_of": d,
                })
            cur.execute(
                """
                SELECT ticker, company_name, sector, current_price, up_and_comer_score,
                       signal, scored_date, theme, bottleneck, thesis, hot_sector,
                       ret_6m_pct, buzz_7d, buzz_accel, vol_trend_d, vol_trend_w,
                       buzz_x_level, buzz_x_rising, buzz_x_note, market_cap,
                       sleeve, fund_score, rev_yoy_pct, piotroski, altman_z, gross_margin_pct,
                       market_regime
                FROM up_and_comers_cache
                WHERE scored_date = (SELECT max(scored_date) FROM up_and_comers_cache)
                ORDER BY up_and_comer_score DESC NULLS LAST
                LIMIT 40
                """
            )
            for (tk, cn, sec, price, score, sig, sdate, theme, bottleneck,
                 thesis, hot_sector, ret6, buzz, baccel, vtd, vtw,
                 xlvl, xris, xnote, mcap,
                 gem_sleeve, fscore, rev_yoy, pio, altz, gmpct,
                 mregime) in cur.fetchall():
                d = str(sdate) if sdate else None
                if d and (as_of is None or d > as_of):
                    as_of = d
                # Prefer the bottleneck thesis as the rationale; fall back to the signal.
                rationale = thesis or (
                    f"{theme} → {bottleneck}" if theme else (sig or "")
                )
                rows.append({
                    "ticker": tk, "sleeve": "gem",
                    "score": float(score) if score is not None else None,
                    "company_name": cn or "",
                    "current_price": float(price) if price is not None else None,
                    "sector": sec or "",
                    "rationale": rationale,
                    "theme": theme or "",
                    "bottleneck": bottleneck or "",
                    "signal": sig or "",
                    "hot_sector": hot_sector or "",
                    "ret_6m_pct": float(ret6) if ret6 is not None else None,
                    "buzz_7d": int(buzz) if buzz is not None else None,
                    "buzz_accel": float(baccel) if baccel is not None else None,
                    "vol_trend_d": float(vtd) if vtd is not None else None,
                    "vol_trend_w": float(vtw) if vtw is not None else None,
                    "buzz_x_level": xlvl or None,
                    "buzz_x_rising": bool(xris) if xris is not None else None,
                    "buzz_x_note": xnote or None,
                    "market_cap": float(mcap) if mcap is not None else None,
                    "gem_sleeve": gem_sleeve or None,
                    "fund_score": float(fscore) if fscore is not None else None,
                    "rev_yoy_pct": float(rev_yoy) if rev_yoy is not None else None,
                    "piotroski": int(pio) if pio is not None else None,
                    "altman_z": float(altz) if altz is not None else None,
                    "gross_margin_pct": float(gmpct) if gmpct is not None else None,
                    "market_regime": mregime or None,
                    "as_of": d,
                })
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {"as_of": as_of, "rows": rows, "count": len(rows)}


_HEAT_WINDOWS = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 91}
# Trading-SESSION lookbacks (not calendar days) — robust to weekends/holidays so
# the daily map never collapses to 0% over a gap. Mirrors migration 0049.
_HEAT_SESSIONS = {"daily": 1, "weekly": 5, "monthly": 21, "quarterly": 63}


def _sector_heat_snapshot(tf: str, weight: str) -> list:
    """Read the precomputed sector heat map (migration 0033 — refreshed nightly
    by ingestion/refresh_vantage.py). Returns [] if the snapshot is missing or
    empty so the caller can fall back to a live compute."""
    from screen.reversal_screen import _conn
    try:
        conn = _conn()
    except Exception:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sector, n, avg_ret, median_ret, capwtd_ret, ret, weight, heat, rank
                FROM sector_heat_snapshot
                WHERE tf = %(tf)s AND weight = %(wt)s
                ORDER BY rank
                """,
                {"tf": tf, "wt": weight},
            )
            rows = cur.fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return [{
        "sector": s, "n": int(n),
        "avg_ret": float(a) if a is not None else 0.0,
        "median_ret": float(m) if m is not None else 0.0,
        "capwtd_ret": float(c) if c is not None else 0.0,
        "ret": float(r) if r is not None else 0.0,
        "weight": w,
        "heat": float(h) if h is not None else 0.5,
        "rank": int(rk),
    } for s, n, a, m, c, r, w, h, rk in rows]


def _sector_heat_live(tf: str = "quarterly", weight: str = "median") -> list:
    """Sector heat map for a timeframe + weighting. Reads the nightly snapshot
    (fast); falls back to a live compute if the snapshot isn't there yet."""
    weight = weight if weight in ("median", "cap") else "median"
    rows = _sector_heat_snapshot(tf, weight)
    if rows:
        return rows
    return _sector_heat_compute(_HEAT_SESSIONS.get(tf, 63), weight)


def _etf_heat_snapshot(tf: str) -> list:
    """Read the precomputed ETF rotation heat map (migration 0051 — refreshed
    nightly by ingestion/refresh_vantage.py). One row per ETF for the timeframe,
    ordered by band then catalog order so the frontend can render labeled bands
    (broad / sector / theme). Returns [] if the snapshot is missing/empty."""
    from screen.reversal_screen import _conn
    try:
        conn = _conn()
    except Exception:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, theme_group, theme_label, ret, heat, rank
                FROM etf_heat_snapshot
                WHERE tf = %(tf)s
                ORDER BY
                    CASE theme_group WHEN 'broad' THEN 0 WHEN 'sector' THEN 1 ELSE 2 END,
                    sort_order, ticker
                """,
                {"tf": tf},
            )
            rows = cur.fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return [{
        "ticker": tk,
        "group": g,
        "label": lbl,
        "ret": float(r) if r is not None else 0.0,
        "heat": float(h) if h is not None else 0.5,
        "rank": int(rk),
    } for tk, g, lbl, r, h, rk in rows]


def _etf_holdings(etf: str, sessions: int) -> list:
    """Full constituent list for an ETF (migration 0053), each enriched with its
    price move over `sessions` trading sessions from daily_prices, plus company
    name and sector from tickers. Ordered by published weight. Holdings not in our
    price universe come back with ret=None (still listed). Powers /api/etf-holdings.

    Session-anchored (not calendar-day) lookback: the move is last_close vs. the
    close `sessions` bars earlier, indexing into the date-desc array. This matches
    the sector/ETF heat MVs and is robust to the latest bar lagging the calendar
    (e.g. before today's close is loaded a 1-session daily move still compares the
    two most recent real sessions instead of collapsing to 0%)."""
    from screen.reversal_screen import _conn
    try:
        conn = _conn()
    except Exception:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH cons AS (
                    SELECT holding_ticker AS ticker, weight
                    FROM etf_constituents WHERE etf_ticker = %(etf)s
                ),
                px AS (
                    SELECT dp.ticker,
                           (array_agg(dp.close ORDER BY dp.trade_date DESC))[1] AS last_close,
                           (array_agg(dp.close ORDER BY dp.trade_date DESC))[1 + %(sessions)s] AS prev_close
                    FROM daily_prices dp
                    WHERE dp.ticker IN (SELECT ticker FROM cons)
                      AND dp.trade_date >= CURRENT_DATE - (%(sessions)s * 2 + 30)
                    GROUP BY dp.ticker
                )
                SELECT c.ticker, c.weight, t.company_name, t.sector,
                       p.last_close,
                       CASE WHEN p.prev_close > 0 THEN p.last_close / p.prev_close - 1 ELSE NULL END AS ret
                FROM cons c
                LEFT JOIN px p     ON p.ticker = c.ticker
                LEFT JOIN tickers t ON t.ticker = c.ticker
                ORDER BY c.weight DESC NULLS LAST, c.ticker
                """,
                {"etf": etf, "sessions": sessions},
            )
            rows = cur.fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = []
    for (tk, w, co, sec, lc, ret) in rows:
        out.append({
            "ticker": tk,
            "weight": float(w) if w is not None else None,
            "company_name": co,
            "sector": sec,
            "price": float(lc) if lc is not None else None,
            "ret": float(ret) if ret is not None else None,
            "in_universe": co is not None,
        })
    return out


def _earnings_calendar_rows(days: int = 14, min_cap: float = 2e9) -> list:
    """Upcoming earnings for the Calendar tab: every watchlist name plus
    mid/large-caps (>= min_cap) reporting in the next `days`, enriched with
    company name, market cap, and trailing-surprise context from
    earnings_calendar (migration 0013). Ordered by date then size."""
    from screen.reversal_screen import _conn
    try:
        conn = _conn()
    except Exception:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ec.ticker, ec.report_date, ec.fiscal_period,
                       ec.eps_estimated, ec.revenue_estimated, ec.time_of_day,
                       ec.last_year_eps, ec.last_quarter_eps, ec.last_4q_surprise_avg,
                       t.company_name, t.sector, COALESCE(t.market_cap, 0) AS market_cap,
                       (w.ticker IS NOT NULL) AS on_watchlist
                FROM earnings_calendar ec
                LEFT JOIN tickers t   ON t.ticker = ec.ticker
                LEFT JOIN watchlist w ON w.ticker = ec.ticker
                WHERE ec.report_date >= CURRENT_DATE
                  AND ec.report_date <= CURRENT_DATE + (%(days)s || ' days')::interval
                  AND (w.ticker IS NOT NULL OR COALESCE(t.market_cap, 0) >= %(cap)s)
                ORDER BY ec.report_date ASC, COALESCE(t.market_cap, 0) DESC
                """,
                {"days": days, "cap": min_cap},
            )
            rows = cur.fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = []
    for (tk, rd, fp, eps, rev, tod, ly, lq, surp, co, sec, cap, wl) in rows:
        out.append({
            "ticker": tk,
            "report_date": rd.isoformat() if rd else None,
            "fiscal_period": fp,
            "eps_estimated": float(eps) if eps is not None else None,
            "revenue_estimated": float(rev) if rev is not None else None,
            "time_of_day": (tod or "").lower() or None,   # 'bmo' | 'amc' | None
            "last_year_eps": float(ly) if ly is not None else None,
            "last_quarter_eps": float(lq) if lq is not None else None,
            "surprise_avg": float(surp) if surp is not None else None,
            "company_name": co,
            "sector": sec,
            "market_cap": float(cap) if cap is not None else 0.0,
            "on_watchlist": bool(wl),
        })
    return out


def _economic_calendar_rows(days: int = 14) -> list:
    """Upcoming macro events for the Calendar tab (next `days`), US + major
    global, from economic_calendar (migration 0052). Returns all impact levels
    ordered by date/time; the frontend pins High-impact and mutes Low.

    event_time is stored in UTC (as FMP delivers it); we convert to America/
    New_York for display so the times read in ET (Postgres handles DST, and the
    date is re-derived from the ET timestamp so a late-UTC foreign print that
    falls on the prior US evening is grouped under the correct ET day)."""
    from screen.reversal_screen import _conn
    try:
        conn = _conn()
    except Exception:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH conv AS (
                    SELECT event_date, country, event, currency, impact,
                           actual, previous, estimate, change_pct, unit,
                           CASE WHEN NULLIF(event_time, '') IS NOT NULL THEN
                               ((event_date::text || ' ' || event_time)::timestamp
                                  AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York'
                           END AS ts_et
                    FROM economic_calendar
                    -- widen the lower bound by a day so a UTC-early event that lands
                    -- on the prior ET evening still surfaces, then re-filter on ET date
                    WHERE event_date >= CURRENT_DATE - 1
                      AND event_date <= CURRENT_DATE + (%(days)s || ' days')::interval
                )
                SELECT COALESCE(ts_et::date, event_date) AS ev_date_et,
                       CASE WHEN ts_et IS NOT NULL THEN to_char(ts_et, 'HH24:MI') ELSE '' END AS ev_time_et,
                       country, event, currency, impact,
                       actual, previous, estimate, change_pct, unit
                FROM conv
                WHERE COALESCE(ts_et::date, event_date) >= CURRENT_DATE
                ORDER BY COALESCE(ts_et, event_date::timestamp) ASC, event ASC
                """,
                {"days": days},
            )
            rows = cur.fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = []
    for (ed, et, ctry, ev, cur_, imp, act, prev, est, chg, unit) in rows:
        out.append({
            "event_date": ed.isoformat() if ed else None,
            "event_time": et or "",                          # ET, "HH:MM" (24h)
            "country": ctry,
            "event": ev,
            "currency": cur_,
            "impact": imp,                                  # High | Medium | Low | None
            "actual": float(act) if act is not None else None,
            "previous": float(prev) if prev is not None else None,
            "estimate": float(est) if est is not None else None,
            "change_pct": float(chg) if chg is not None else None,
            "unit": unit,
        })
    return out


def _sector_heat_compute(sessions_back: int = 63, weight: str = "median") -> list:
    """Live sector heat map: rank every GICS sector hottest->coldest by price
    momentum over `sessions_back` TRADING SESSIONS (last bar vs the bar N sessions
    ago — robust to weekends/holidays), across real common stocks. Colors relative
    to the spread within the chosen window, so the map is readable at any horizon
    (daily..quarterly).

    weight = "median" → the MEDIAN stock (breadth: how the typical stock did;
                        robust — the average is skewed by a few micro-cap moonshots).
    weight = "cap"    → CAP-WEIGHTED mean (the sector index / ETF view — what
                        Finviz/CNBC show; dominated by the mega-caps).
    Both are computed every call; `weight` only decides which one drives the
    displayed number, ranking and color."""
    use_cap = (weight == "cap")
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH px AS (
                    SELECT dp.ticker, array_agg(dp.close ORDER BY dp.trade_date DESC) AS closes
                    FROM daily_prices dp
                    WHERE dp.trade_date >= CURRENT_DATE - 150
                    GROUP BY dp.ticker
                ), ret AS (
                    SELECT t.sector, COALESCE(t.market_cap, 0) AS mcap,
                           p.closes[1] / NULLIF(p.closes[%(nb)s], 0) - 1 AS r
                    FROM px p JOIN tickers t ON t.ticker = p.ticker
                    WHERE t.delisted = false AND t.sector IS NOT NULL
                      AND t.industry NOT ILIKE '%%Asset Management%%'
                      AND t.company_name NOT ILIKE '%% ETF%%'
                      AND t.company_name NOT ILIKE '%% Fund%%'
                      AND COALESCE(t.market_cap, 0) >= 50000000
                      AND p.closes[1] >= 1.50 AND p.closes[%(nb)s] > 0
                )
                SELECT sector, COUNT(*) n, AVG(r) avg_ret,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r) median_ret,
                       SUM(r * mcap) / NULLIF(SUM(mcap), 0) AS capwtd_ret
                FROM ret WHERE r IS NOT NULL
                GROUP BY sector HAVING COUNT(*) >= 5
                """,
                {"nb": sessions_back + 1},
            )
            raw = [(s, int(n), float(a or 0.0), float(m or 0.0),
                    (float(c) if c is not None else float(m or 0.0)))
                   for s, n, a, m, c in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not raw:
        return []
    # the metric that drives ranking + color, per the chosen weighting
    vals = [(c if use_cap else m) for _, _, _, m, c in raw]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    out = [{
        "sector": s, "n": n,
        "avg_ret": round(a, 4),
        "median_ret": round(m, 4),
        "capwtd_ret": round(c, 4),
        # `ret` = the value actually displayed, given the weighting
        "ret": round(c if use_cap else m, 4),
        "weight": ("cap" if use_cap else "median"),
        # heat = where this sector sits between the coldest (0) and hottest (1)
        # sector for THIS window — always spans the full spectrum.
        "heat": round(((c if use_cap else m) - lo) / span, 3),
    } for s, n, a, m, c in raw]
    out.sort(key=lambda r: r["ret"], reverse=True)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def _gems_rows() -> dict:
    """Latest hidden-gem candidates with full bottleneck context, for the
    dedicated Hidden Gems section (heat map + thesis cards)."""
    gems = [r for r in _swing_rows()["rows"] if r.get("sleeve") == "gem"]
    as_of = gems[0]["as_of"] if gems else None
    regime = next((g.get("market_regime") for g in gems if g.get("market_regime")), None)
    try:
        heat = _sector_heat_live()
    except Exception as e:
        heat = []
        log.warning("sector heat compute failed: %s", e)
    return {"as_of": as_of, "gems": gems, "heat": heat,
            "market_regime": regime, "count": len(gems)}


def _rotation_rows() -> dict:
    """Latest sector-rotation read (watchtower migration 0034): which sectors are
    seeing early money inflow/outflow, plus Grok's narrative. Median (breadth) is
    the primary signal; cap-weighted confirms; breadth-led = the earliest move."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT as_of, regime, narrative, source, rotating_in, rotating_out
                FROM sector_rotation_read ORDER BY as_of DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return {"as_of": None, "narrative": None, "rotating_in": [], "rotating_out": [], "sectors": []}
            as_of, regime, narrative, source, r_in, r_out = row
            cur.execute(
                """
                SELECT sector, state, accel_median, breadth_led, week_rank_med,
                       month_rank_med, qtr_rank_med, week_ret_med, month_ret_med, qtr_ret_med
                FROM sector_rotation WHERE as_of = %s ORDER BY accel_median DESC
                """,
                (as_of,),
            )
            sectors = [{
                "sector": s, "state": st, "accel_median": int(a) if a is not None else 0,
                "breadth_led": bool(bl), "week_rank": wr, "month_rank": mr, "qtr_rank": qr,
                "week_ret": float(wt) if wt is not None else None,
                "month_ret": float(mt) if mt is not None else None,
                "qtr_ret": float(qt) if qt is not None else None,
            } for (s, st, a, bl, wr, mr, qr, wt, mt, qt) in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {"as_of": str(as_of), "regime": regime, "narrative": narrative,
            "source": source, "rotating_in": r_in or [], "rotating_out": r_out or [],
            "sectors": sectors}


def _gem_performance() -> dict:
    """Forward performance of every hidden-gem pick (gem_pick_history, migration
    0037) at 7/30/90 calendar days — overall and by sleeve. Only picks with
    enough elapsed time count toward each horizon; the rest are still maturing."""
    from screen.reversal_screen import _conn
    base = """
        WITH picks AS (
            SELECT pick_date, ticker, entry_price, sleeve FROM gem_pick_history
            WHERE entry_price > 0
        ), r AS (
            SELECT {grp_col} AS grp_val,
              CASE WHEN p.pick_date <= CURRENT_DATE - 7 THEN
                (SELECT close FROM daily_prices d WHERE d.ticker=p.ticker AND d.trade_date >= p.pick_date+7  ORDER BY d.trade_date LIMIT 1)/NULLIF(p.entry_price,0)-1 END AS r7,
              CASE WHEN p.pick_date <= CURRENT_DATE - 30 THEN
                (SELECT close FROM daily_prices d WHERE d.ticker=p.ticker AND d.trade_date >= p.pick_date+30 ORDER BY d.trade_date LIMIT 1)/NULLIF(p.entry_price,0)-1 END AS r30,
              CASE WHEN p.pick_date <= CURRENT_DATE - 90 THEN
                (SELECT close FROM daily_prices d WHERE d.ticker=p.ticker AND d.trade_date >= p.pick_date+90 ORDER BY d.trade_date LIMIT 1)/NULLIF(p.entry_price,0)-1 END AS r90
            FROM picks p
        )
        SELECT grp_val,
            count(r7)  AS n7,  avg(r7)  AS a7,  avg((r7>0)::int)::float  AS w7,
            count(r30) AS n30, avg(r30) AS a30, avg((r30>0)::int)::float AS w30,
            count(r90) AS n90, avg(r90) AS a90, avg((r90>0)::int)::float AS w90
        FROM r GROUP BY grp_val ORDER BY grp_val
    """
    out = {"horizons": [7, 30, 90]}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), min(pick_date), max(pick_date) FROM gem_pick_history")
            n, first, last = cur.fetchone()
            out["total_picks"] = int(n or 0)
            out["first_day"] = str(first) if first else None
            out["last_day"] = str(last) if last else None
            cur.execute(base.format(grp_col="'all'"))
            overall = cur.fetchall()
            cur.execute(base.format(grp_col="p.sleeve"))
            by_sleeve = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    def pack(rows):
        res = []
        for (gv, n7, a7, w7, n30, a30, w30, n90, a90, w90) in rows:
            res.append({
                "group": gv,
                "d7":  {"n": int(n7 or 0),  "avg": float(a7)  if a7  is not None else None, "win": float(w7)  if w7  is not None else None},
                "d30": {"n": int(n30 or 0), "avg": float(a30) if a30 is not None else None, "win": float(w30) if w30 is not None else None},
                "d90": {"n": int(n90 or 0), "avg": float(a90) if a90 is not None else None, "win": float(w90) if w90 is not None else None},
            })
        return res
    out["overall"] = pack(overall)
    out["by_sleeve"] = pack(by_sleeve)
    return out


def _gem_departures() -> dict:
    """Names that fell off the Hidden Gems list at the latest scan, each tagged
    with WHY it left (migration 0048): industry_cooled (sector rotated out),
    out_ranked (missed the cutoff), too_extended / broke_30w_base / blew_off /
    size_out_of_band / left_universe."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT gd.ticker, t.company_name, gd.prev_score, gd.prev_sleeve,
                       gd.reason, gd.detail, gd.scored_date
                FROM gem_departures gd
                LEFT JOIN tickers t ON t.ticker = gd.ticker
                WHERE gd.scored_date = (SELECT max(scored_date) FROM gem_departures)
                ORDER BY gd.prev_score DESC NULLS LAST
                """
            )
            rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    as_of = rows[0][6] if rows else None
    out = [{
        "ticker": r[0], "company": r[1],
        "prev_score": float(r[2]) if r[2] is not None else None,
        "sleeve": r[3], "reason": r[4], "detail": r[5],
    } for r in rows]
    return {"as_of": str(as_of) if as_of else None, "rows": out, "count": len(out)}


# Whitelisted sort keys -> ORDER BY (keys are fixed, safe to inline)
_SCREENER_SORTS = {
    "score":  "g.up_and_comer_score DESC NULLS LAST",
    "rs":     "s.rs_pct DESC NULLS LAST",
    "1m":     "s.ret_1m DESC NULLS LAST",
    "3m":     "s.ret_3m DESC NULLS LAST",
    "6m":     "s.ret_6m DESC NULLS LAST",
    "rev":    "s.rev_yoy DESC NULLS LAST",
    "mktcap": "s.market_cap DESC NULLS LAST",
    "ticker": "s.ticker ASC",
}


# Market-cap bands (cumulative ceilings). Default keeps the gem window so the
# default Screener view == the gem-gate pool; wider bands reach into large/mega.
_SCREENER_CAPS = {
    "gem":   10e9,    # < $10B — the hidden-gem window (default)
    "large": 50e9,    # < $50B — adds large-caps (e.g. DKS ~$19B)
    "all":   None,    # no ceiling
}


def _screener_rows(sector: str = "ALL", sort: str = "score", gems_only: bool = False,
                   cap: str = "gem", industry: str = "", search: str = "") -> dict:
    """Full gem-screener pool (migrations 0035/0036): every name that clears the
    gem gates, filterable by sector, market-cap band, and (for radar drill-down)
    a specific industry, with the live gem score joined in. `search` matches
    ticker or company name server-side, so a name past the row limit is still
    findable."""
    order = _SCREENER_SORTS.get(sort, _SCREENER_SORTS["score"])
    cap_ceiling = _SCREENER_CAPS.get(cap, _SCREENER_CAPS["gem"])
    industry = (industry or "").strip()
    # An explicit ticker/name search overrides the cap band: "KMI" typed into
    # the filter box means "find me KMI", not "find KMI if it's under $10B" —
    # the old behavior returned nothing and read as the name being missing.
    if (search or "").strip():
        cap_ceiling = None
    qpat = "%" + (search or "").strip() + "%"   # '%%' when blank → matches all
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sector, count(*) FROM screener_snapshot "
                "WHERE (%(capc)s IS NULL OR market_cap < %(capc)s) "
                "GROUP BY sector ORDER BY count(*) DESC",
                {"capc": cap_ceiling},
            )
            sectors = [{"sector": s, "n": int(n)} for s, n in cur.fetchall()]
            cur.execute(
                f"""
                SELECT s.ticker, s.company_name, s.sector, s.industry, s.market_cap, s.price,
                       s.ret_1m, s.ret_3m, s.ret_6m, s.vs_sma, s.rev_yoy, s.rs_pct,
                       s.is_parabolic, s.is_recent_ipo,
                       s.piotroski_score, s.altman_z_score, s.gross_margin,
                       g.up_and_comer_score, g.theme, g.sleeve
                FROM screener_snapshot s
                LEFT JOIN up_and_comers_cache g
                  ON g.ticker = s.ticker
                 AND g.scored_date = (SELECT max(scored_date) FROM up_and_comers_cache)
                WHERE (%(sec)s = 'ALL' OR s.sector = %(sec)s)
                  AND (%(ind)s = '' OR s.industry = %(ind)s)
                  AND (%(go)s = false OR g.up_and_comer_score IS NOT NULL)
                  AND (%(capc)s IS NULL OR s.market_cap < %(capc)s)
                  AND (s.ticker ILIKE %(qpat)s OR s.company_name ILIKE %(qpat)s)
                ORDER BY {order}, s.market_cap DESC NULLS LAST
                LIMIT 1200
                """,
                {"sec": sector, "go": gems_only, "capc": cap_ceiling,
                 "ind": industry, "qpat": qpat},
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass
    def _f(v):
        return float(v) if v is not None else None
    out = [{
        "ticker": r["ticker"], "company_name": r["company_name"], "sector": r["sector"],
        "industry": r["industry"], "market_cap": _f(r["market_cap"]), "price": _f(r["price"]),
        "ret_1m": _f(r["ret_1m"]), "ret_3m": _f(r["ret_3m"]), "ret_6m": _f(r["ret_6m"]),
        "vs_sma": _f(r["vs_sma"]), "rev_yoy": _f(r["rev_yoy"]),
        "rs_pct": int(r["rs_pct"]) if r["rs_pct"] is not None else None,
        "is_parabolic": bool(r["is_parabolic"]), "is_recent_ipo": bool(r["is_recent_ipo"]),
        "piotroski": int(r["piotroski_score"]) if r["piotroski_score"] is not None else None,
        "altman_z": _f(r["altman_z_score"]), "gross_margin": _f(r["gross_margin"]),
        "gem_score": _f(r["up_and_comer_score"]), "gem_theme": r["theme"], "gem_sleeve": r["sleeve"],
    } for r in rows]
    return {"sectors": sectors, "rows": out, "count": len(out),
            "total": sum(s["n"] for s in sectors), "industry": industry}


def _early_turn_rows(limit: int = 18) -> dict:
    """Early-turn / coiling-sector radar (migration 0038): industries whose SHORT
    window (2-week breadth + volume) is turning up before they're '3-month hot'.
    Lower-conviction by design (early = more head-fakes) — a watch, not a trigger."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT industry, sector, n, r1w_med, r2w_med, r1m_med, r3m_med,
                       breadth_2w, vol_surge, leaders, early_score
                FROM industry_pulse WHERE state='early_turn'
                ORDER BY early_score DESC LIMIT %s
                """, (limit,),
            )
            rows = cur.fetchall()
            cur.execute("SELECT max(as_of) FROM industry_pulse")
            as_of = cur.fetchone()[0]
    finally:
        try:
            conn.close()
        except Exception:
            pass
    def _f(v):
        return float(v) if v is not None else None
    out = [{
        "industry": r[0], "sector": r[1], "n": int(r[2]),
        "r1w": _f(r[3]), "r2w": _f(r[4]), "r1m": _f(r[5]), "r3m": _f(r[6]),
        "breadth": _f(r[7]), "vol_surge": _f(r[8]),
        "leaders": list(r[9] or []), "score": _f(r[10]),
    } for r in rows]
    return {"as_of": str(as_of) if as_of else None, "rows": out, "count": len(out)}


# ── Bearish early rotation ───────────────────────────────────────────────────
# The mirror of the Early-Turn radar. industry_pulse already computes a
# 'cooling' state nightly (2-wk median <= -4% while the 3-month is still
# positive — "was working, now cracking"); until now nothing surfaced it.

_RISK_DEFENSIVE = ("GLD", "XLU", "XLP", "XLV")
_RISK_OFFENSE = ("XLK", "XLY", "QQQ", "SMH", "IWM")


def _bearish_rotation_rows(limit: int = 14) -> dict:
    """Early Turn ↓: cooling industries ranked by a distribution-weighted fade
    score, a defensives-vs-offense risk gauge, and the weak liquid names inside
    the cooling groups (put-scouting list, flagged when a live bearish chart
    pattern backs the fundamentals-of-the-tape read)."""
    from statistics import median
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            # Cooling industries. Fade score favors: hard 2-wk drop, decay on
            # ABOVE-average volume (distribution), collapsed breadth, and a
            # 1-wk that's accelerating below the 2-wk pace.
            cur.execute(
                """
                SELECT industry, sector, n, r1w_med, r2w_med, r1m_med, r3m_med,
                       breadth_2w, vol_surge, leaders,
                       round( (-r2w_med*100) * (1 + COALESCE(vol_surge,1)/2)
                             + GREATEST(0, 0.55 - COALESCE(breadth_2w,0.5)) * 60
                             + CASE WHEN r1w_med < r2w_med/2 THEN 10 ELSE 0 END, 1)
                       AS fade_score
                FROM industry_pulse WHERE state = 'cooling'
                ORDER BY fade_score DESC LIMIT %s
                """, (limit,),
            )
            cooling = [{
                "industry": r[0], "sector": r[1], "n": int(r[2]),
                "r1w": float(r[3]) if r[3] is not None else None,
                "r2w": float(r[4]) if r[4] is not None else None,
                "r1m": float(r[5]) if r[5] is not None else None,
                "r3m": float(r[6]) if r[6] is not None else None,
                "breadth": float(r[7]) if r[7] is not None else None,
                "vol_surge": float(r[8]) if r[8] is not None else None,
                "leaders": list(r[9] or []),
                "fade_score": float(r[10]) if r[10] is not None else None,
            } for r in cur.fetchall()]

            # Risk gauge: defensives (GLD/XLU/XLP/XLV) vs offense
            # (XLK/XLY/QQQ/SMH/IWM) on the weekly ETF heat window. Defensives
            # leading = the cooling above is risk-off rotation, not noise.
            cur.execute(
                "SELECT ticker, ret FROM etf_heat_snapshot "
                "WHERE tf = 'weekly' AND ticker = ANY(%s)",
                (list(_RISK_DEFENSIVE + _RISK_OFFENSE + ("SPY", "HYG")),),
            )
            rets = {t: float(x) for t, x in cur.fetchall() if x is not None}
            d_ = [rets[t] for t in _RISK_DEFENSIVE if t in rets]
            o_ = [rets[t] for t in _RISK_OFFENSE if t in rets]
            spread = (median(d_) - median(o_)) * 100 if d_ and o_ else None
            risk_state = None
            if spread is not None:
                risk_state = ("risk_off" if spread >= 1.5
                              else "risk_on" if spread <= -1.5 else "neutral")
            cur.execute(
                "SELECT market_regime FROM up_and_comers_cache "
                "WHERE market_regime IS NOT NULL "
                "ORDER BY scored_date DESC LIMIT 1"
            )
            row = cur.fetchone()
            regime = row[0] if row else None
            risk = {
                "state": risk_state,
                "spread": round(spread, 2) if spread is not None else None,
                "defensive_w": round(median(d_) * 100, 2) if d_ else None,
                "offense_w": round(median(o_) * 100, 2) if o_ else None,
                "spy_w": round(rets["SPY"] * 100, 2) if "SPY" in rets else None,
                "hyg_w": round(rets["HYG"] * 100, 2) if "HYG" in rets else None,
                "regime": regime,
            }

            # Put candidates: weak + liquid names inside the cooling groups.
            # Cap/price floors keep the options chains tradeable; the bearish
            # pattern bonus (forming preferred — that's the early entry) ties
            # this list to the Patterns engine.
            cur.execute(
                """
                WITH cooling AS (
                    SELECT industry FROM industry_pulse WHERE state = 'cooling'
                ),
                bear_pat AS (
                    SELECT ticker, count(*) AS n,
                           bool_or(status IN ('forming', 'retest')) AS any_forming,
                           (array_agg(pattern  ORDER BY score DESC NULLS LAST))[1] AS top_pattern,
                           (array_agg(status   ORDER BY score DESC NULLS LAST))[1] AS top_status,
                           (array_agg(timeframe ORDER BY score DESC NULLS LAST))[1] AS top_tf
                    FROM pattern_scan WHERE direction = 'bearish'
                    GROUP BY ticker
                )
                SELECT s.ticker, s.company_name, s.sector, s.industry,
                       s.rs_pct, s.ret_1m, s.vs_sma, s.market_cap, s.price,
                       bp.n, bp.top_pattern, bp.top_status, bp.top_tf,
                       round( GREATEST(0, 60 - COALESCE(s.rs_pct, 50)) * 0.5
                             + GREATEST(0, -s.ret_1m * 100) * 0.6
                             + GREATEST(0, -s.vs_sma * 100) * 0.5
                             + CASE WHEN bp.n IS NOT NULL THEN 15 ELSE 0 END
                             + CASE WHEN bp.any_forming THEN 5 ELSE 0 END, 1)
                       AS weak_score
                FROM screener_snapshot s
                JOIN cooling c ON c.industry = s.industry
                LEFT JOIN bear_pat bp ON bp.ticker = s.ticker
                WHERE s.market_cap > 2e9 AND s.price > 15
                  AND COALESCE(s.rs_pct, 50) < 60
                  AND s.ret_1m < 0 AND s.vs_sma < 0
                  AND NOT s.is_recent_ipo
                ORDER BY weak_score DESC
                LIMIT 16
                """
            )
            def _f(v):
                return float(v) if v is not None else None
            puts = [{
                "ticker": r[0], "company_name": r[1] or "", "sector": r[2] or "",
                "industry": r[3] or "",
                "rs_pct": int(r[4]) if r[4] is not None else None,
                "ret_1m": _f(r[5]), "vs_sma": _f(r[6]),
                "market_cap": _f(r[7]), "price": _f(r[8]),
                "bear_patterns": int(r[9]) if r[9] is not None else 0,
                "top_pattern": r[10], "top_status": r[11], "top_tf": r[12],
                "weak_score": _f(r[13]),
            } for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {"cooling": cooling, "risk": risk, "puts": puts}


# Window key -> materialized-view column stem. Ladder: 1w 2w 1m 3m 6m ytd.
_THEME_WINDOWS = {"1w": "r1w", "2w": "r2w", "1m": "r1m",
                  "3m": "r3m", "6m": "r6m", "ytd": "rytd"}


def _theme_rows(window: str = "ytd", weight: str = "median") -> dict:
    """Market Themes board (migrations 0040/0041): thematic-basket returns over a
    window (1w|2w|1m|3m|6m|ytd), ranked best-first. weight=median is the breadth
    read (typical member); weight=cap is the index read (cap-weighted)."""
    from screen.reversal_screen import _conn
    win = _THEME_WINDOWS.get((window or "ytd").lower(), "rytd")
    suffix = "cap" if (weight or "median").lower() == "cap" else "med"
    col = f"{win}_{suffix}"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            # Carry the whole ladder for the active weighting so the tile can
            # show a mini multi-window read alongside the selected window.
            stems = ["r1w", "r2w", "r1m", "r3m", "r6m", "rytd"]
            ladder = ", ".join(f"tp.{s}_{suffix}" for s in stems)
            cur.execute(
                f"""
                SELECT tp.theme, tp.n, tp.{col} AS ret, {ladder}, tp.vol_med,
                       tp.leaders, COALESCE(td.sort_order, 100) AS so
                FROM theme_performance tp
                LEFT JOIN theme_defs td ON td.theme = tp.theme
                ORDER BY tp.{col} DESC NULLS LAST
                """,
            )
            rows = cur.fetchall()
            cur.execute("SELECT max(as_of) FROM theme_performance")
            as_of = cur.fetchone()[0]
    finally:
        try:
            conn.close()
        except Exception:
            pass

    def _f(v):
        return float(v) if v is not None else None
    out = [{
        "theme": r[0], "n": int(r[1]), "ret": _f(r[2]),
        "1w": _f(r[3]), "2w": _f(r[4]), "1m": _f(r[5]),
        "3m": _f(r[6]), "6m": _f(r[7]), "ytd": _f(r[8]),
        "vol": _f(r[9]), "leaders": list(r[10] or []),
    } for r in rows]
    return {"as_of": str(as_of) if as_of else None, "window": window,
            "weight": ("cap" if suffix == "cap" else "median"),
            "rows": out, "count": len(out)}


def _theme_members(theme: str, window: str = "ytd") -> dict:
    """Drill-down for one theme tile: every stock in the basket with its return
    across the full window ladder, sorted by the selected window (best first)."""
    from screen.reversal_screen import _conn
    sort_col = _THEME_WINDOWS.get((window or "ytd").lower(), "rytd")
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT ticker, company_name, sector, industry, market_cap,
                       r1w, r2w, r1m, r3m, r6m, rytd, vol_surge
                FROM theme_member_perf
                WHERE theme = %s
                ORDER BY {sort_col} DESC NULLS LAST
                """, (theme,),
            )
            rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    def _f(v):
        return float(v) if v is not None else None
    out = [{
        "ticker": r[0], "company": r[1], "sector": r[2], "industry": r[3],
        "market_cap": _f(r[4]),
        "1w": _f(r[5]), "2w": _f(r[6]), "1m": _f(r[7]),
        "3m": _f(r[8]), "6m": _f(r[9]), "ytd": _f(r[10]), "vol": _f(r[11]),
    } for r in rows]
    return {"theme": theme, "window": window, "rows": out, "count": len(out)}


# ── Vantage: fundamentals map ──────────────────────────────────────────────
# A sector/index ranked into color-graded tiles by ONE fundamental. Each metric
# maps to a COLUMN in the vantage_snapshot materialized view (precomputed daily —
# see migration 0031), whether higher is better (quality/growth/yield) or lower
# is better (valuation multiples), positive-only, and a display formatter. Keys
# are a fixed whitelist, so inlining the column into SQL is safe.
#   (label, snapshot_column, higher_is_better, positive_only, fmt)
_VANTAGE_METRICS = {
    "pe":               ("Trailing P/E",          "pe",                False, True,  "mult"),
    "forward_pe":       ("Forward P/E",           "forward_pe",        False, True,  "mult"),
    "ps":               ("P/S",                   "ps",                False, True,  "mult"),
    "ev_ebitda":        ("EV/EBITDA",             "ev_ebitda",         False, True,  "mult"),
    "pb":               ("P/B",                   "pb",                False, True,  "mult"),
    "fcf_yield":        ("FCF Yield",             "fcf_yield",         True,  False, "pct"),
    "roe":              ("ROE",                   "roe",               True,  False, "pct"),
    "roic":             ("ROIC",                  "roic",              True,  False, "pct"),
    "gross_margin":     ("Gross Margin",          "gross_margin",      True,  False, "pct"),
    "operating_margin": ("Operating Margin",      "operating_margin",  True,  False, "pct"),
    "rev_growth":       ("Revenue Growth (YoY)",  "rev_yoy",           True,  False, "pct"),
    "piotroski":        ("Piotroski F-Score",     "piotroski_score",   True,  False, "score9"),
    "altman_z":         ("Altman Z-Score",        "altman_z_score",    True,  False, "znum"),
}


# Market-cap floor for the Vantage tiles. Default $2B keeps the map to sizable
# names; lower it to pull small-caps (e.g. a $450M hidden gem) into the grid.
_VANTAGE_MINCAPS = {"2b": 2e9, "500m": 5e8, "250m": 2.5e8, "all": 0.0}


def _vantage_fmt(v: float, fmt: str) -> str:
    if fmt == "mult":
        return f"{v:.1f}×"
    if fmt == "pct":
        return f"{v * 100:.1f}%"
    if fmt == "score9":
        return f"{int(round(v))}/9"
    if fmt == "znum":
        return f"{v:.1f}"
    return f"{v:.2f}"


def _vantage_rows(metric: str, universe: str, color: str,
                  mincap: float = 2e9, limit: int = 400) -> dict:
    meta = _VANTAGE_METRICS.get(metric)
    if not meta:
        return {"tiles": [], "count": 0, "error": f"unknown metric: {metric}"}
    label, col, higher, positive_only, fmt = meta
    universe = (universe or "ALL").strip() or "ALL"
    color = "sector" if color == "sector" else "abs"
    pos_filter = f" AND {col} > 0" if positive_only else ""
    order = "DESC" if higher else "ASC"   # best (green) first

    # Reads the precomputed vantage_snapshot (one row per valued ticker, refreshed
    # daily) instead of re-deriving per-ticker metrics over ~230k fundamentals
    # rows on every request. `col` is a fixed-whitelist column name.
    sql = f"""
        WITH base AS (
            SELECT ticker, company_name, sector, market_cap, {col} AS val
            FROM vantage_snapshot
            WHERE {col} IS NOT NULL{pos_filter}
              AND COALESCE(market_cap, 0) >= %(mincap)s
              AND (%(uni)s = 'ALL' OR sector = %(uni)s)
        ), ranked AS (
            SELECT ticker, company_name, sector, market_cap, val,
                   percent_rank() OVER (ORDER BY val) AS p_abs,
                   percent_rank() OVER (PARTITION BY sector ORDER BY val) AS p_sector,
                   row_number() OVER (ORDER BY market_cap DESC NULLS LAST) AS mc_rank
            FROM base
        )
        SELECT ticker, company_name, sector, market_cap, val, p_abs, p_sector
        FROM ranked WHERE mc_rank <= %(limit)s
        ORDER BY val {order}
    """

    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT max(as_of_date) FROM valuation_metrics")
            row = cur.fetchone()
            as_of = row[0] if row else None
            cur.execute(sql, {"uni": universe, "mincap": mincap, "limit": limit})
            rows = cur.fetchall()
    finally:
        conn.close()

    tiles = []
    for (tk, cn, sec, mcap, val, p_abs, p_sec) in rows:
        v = float(val)
        g_abs = float(p_abs) if higher else (1.0 - float(p_abs))
        g_sec = float(p_sec) if higher else (1.0 - float(p_sec))
        tiles.append({
            "ticker": tk, "company": cn, "sector": sec,
            "market_cap": float(mcap) if mcap is not None else None,
            "value": round(v, 4), "display": _vantage_fmt(v, fmt),
            "g_abs": round(g_abs, 4), "g_sector": round(g_sec, 4),
        })

    return {
        "metric": metric, "metric_label": label, "lower_is_better": (not higher),
        "universe": universe, "color": color,
        "as_of": str(as_of) if as_of else None,
        "count": len(tiles), "tiles": tiles,
    }


_VANTAGE_COLS = ["pe", "forward_pe", "ps", "ev_ebitda", "pb", "fcf_yield", "roe",
                 "roic", "gross_margin", "operating_margin", "rev_yoy",
                 "piotroski_score", "altman_z_score"]


def _vantage_lookup(ticker: str) -> dict:
    """A single ticker's row from the snapshot — so the search box can locate it
    even when it's filtered out of the current view (wrong sector / no positive
    earnings) and explain why."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT company_name, sector, market_cap, " + ", ".join(_VANTAGE_COLS)
                + " FROM vantage_snapshot WHERE ticker = %s", (ticker,))
            r = cur.fetchone()
    finally:
        conn.close()
    if not r:
        return {"found": False, "ticker": ticker}
    metrics = {c: (float(r[3 + i]) if r[3 + i] is not None else None)
               for i, c in enumerate(_VANTAGE_COLS)}
    return {"found": True, "ticker": ticker, "company": r[0], "sector": r[1],
            "market_cap": float(r[2]) if r[2] is not None else None, "metrics": metrics}


_FMP_BASE = "https://financialmodelingprep.com/stable"


def _company_profile(ticker: str) -> dict:
    """Company reference + a plain-English description of what the business does,
    for the bottom of the ticker drawer.

    Reads tickers.description from the DB (populated by the weekly profile ingest).
    If it's missing — a name we haven't profiled yet, or one added before the
    description column existed — fetch it from FMP once and persist it, so every
    subsequent open is an instant DB read.
    """
    from screen.reversal_screen import _conn
    out = {"company_name": None, "sector": None, "industry": None,
           "country": None, "market_cap": None, "description": None}
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT company_name, sector, industry, country, market_cap, description "
                "FROM tickers WHERE ticker = %s", (ticker,))
            r = cur.fetchone()
        if r:
            out.update(company_name=r[0], sector=r[1], industry=r[2], country=r[3],
                       market_cap=float(r[4]) if r[4] is not None else None,
                       description=(r[5] or None))
        # Lazily backfill the description from FMP the first time we need it.
        if not out["description"]:
            desc = _fmp_description(ticker)
            if desc:
                out["description"] = desc
                with conn.cursor() as cur:
                    cur.execute("UPDATE tickers SET description = %s WHERE ticker = %s",
                                (desc, ticker))
                conn.commit()
    except Exception as e:
        log.warning(f"[dashboard.api] _company_profile({ticker}) failed: {e}")
    finally:
        conn.close()
    return out


def _ticker_memberships(ticker: str) -> dict:
    """Which heat-map ETF tiles and theme baskets hold this ticker — the
    drawer's "belongs to" line. Completes the drill-down loop in reverse: from
    a single runner back to the chartable ETF whose breakout it follows, and
    the theme basket it trades with. Pure DB reads over etf_constituents /
    etf_theme_map / theme_members."""
    from screen.reversal_screen import _conn
    out = {"etfs": [], "themes": []}
    try:
        conn = _conn()
    except Exception:
        return out
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.etf_ticker, m.theme_label, m.theme_group, c.weight
                FROM etf_constituents c
                JOIN etf_theme_map m ON m.ticker = c.etf_ticker
                WHERE c.holding_ticker = %s
                ORDER BY CASE m.theme_group
                           WHEN 'theme' THEN 0 WHEN 'sector' THEN 1 ELSE 2 END,
                         c.weight DESC NULLS LAST
                """,
                (ticker,),
            )
            out["etfs"] = [
                {"ticker": r[0], "label": r[1], "group": r[2],
                 "weight": float(r[3]) if r[3] is not None else None}
                for r in cur.fetchall()
            ]
            cur.execute("SELECT theme FROM theme_members WHERE ticker = %s ORDER BY theme",
                        (ticker,))
            out["themes"] = [r[0] for r in cur.fetchall()]
            # Relative strength for ANY priced ticker, computed live against the
            # screener universe's momentum composite (1m + 2*3m + 6m, session-
            # anchored). The MV's own rs_pct only covers names that pass the
            # screener gates — which excludes exactly the hottest names (the
            # >150%-in-6m "blown off" gate), and those are the ones you open.
            cur.execute(
                """
                WITH me AS (
                    SELECT (closes[1]/NULLIF(closes[22],0)-1)  AS r1m,
                           (closes[1]/NULLIF(closes[64],0)-1)  AS r3m,
                           (closes[1]/NULLIF(closes[127],0)-1) AS r6m
                    FROM (SELECT array_agg(close ORDER BY trade_date DESC) AS closes
                          FROM daily_prices
                          WHERE ticker = %s AND trade_date >= CURRENT_DATE - 280) z
                )
                SELECT (1 + round(
                    (SELECT count(*) FROM screener_snapshot s
                     WHERE COALESCE(s.ret_1m,0) + 2*COALESCE(s.ret_3m,0) + COALESCE(s.ret_6m,0)
                         < COALESCE(me.r1m,0) + 2*COALESCE(me.r3m,0) + COALESCE(me.r6m,0)
                    )::numeric
                    / NULLIF((SELECT count(*) FROM screener_snapshot), 0) * 98))::int
                FROM me
                WHERE me.r3m IS NOT NULL
                """,
                (ticker,),
            )
            r = cur.fetchone()
            out["rs_pct"] = int(r[0]) if r and r[0] is not None else None
    except Exception as e:
        log.warning(f"[dashboard.api] _ticker_memberships({ticker}) failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


_PATTERN_TIMING_CACHE = {"at": 0.0, "stats": {}}


def _pattern_timing_cached() -> dict:
    """Backtest timing stats (bars from breakout to target per pattern),
    cached for 6h — they only change when the replay re-runs."""
    import time as _time
    now = _time.time()
    if now - _PATTERN_TIMING_CACHE["at"] > 6 * 3600:
        try:
            from analysis.pattern_backtest import timing_stats
            _PATTERN_TIMING_CACHE["stats"] = timing_stats()
        except Exception as e:
            log.debug(f"[dashboard.api] pattern timing stats unavailable: {e}")
            _PATTERN_TIMING_CACHE["stats"] = {}
        _PATTERN_TIMING_CACHE["at"] = now
    return _PATTERN_TIMING_CACHE["stats"]


def _pattern_rows(tf: str = "all", status: str = "all", direction: str = "all",
                  pattern: str = "ALL", search: str = "") -> dict:
    """Live chart-pattern detections (pattern_scan, migration 0061) with the
    screener's RS/sector context joined in. The scan only ever keeps live
    patterns, so no recency filtering is needed here. The pattern filter
    must be applied HERE, not client-side: the book runs thousands of rows
    and the 800-row score cap would otherwise silently drop most of any
    one pattern's setups before the browser ever saw them."""
    tf = tf if tf in ("weekly", "daily", "4h") else "all"
    status = status if status in ("forming", "retest", "breakout") else "all"
    direction = direction if direction in ("bullish", "bearish") else "all"
    pattern = (pattern or "ALL").strip()
    # Ticker search must ALSO run in SQL: the payload is capped at the top
    # 800 by score, so a client-side search over it silently misses any
    # matching name below the cap (AAL's score-63 bounce vs 6,300 rows).
    search = (search or "").strip().upper()[:12]
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.ticker, p.timeframe, p.pattern, p.direction, p.status,
                       p.trigger_price, p.target, p.invalid_level, p.last_close,
                       p.dist_to_trigger_pct, p.score, p.points, p.anchor_date,
                       p.detected_at, p.scanned_at,
                       s.company_name, s.sector, s.rs_pct,
                       w.direction AS weekly_dir,
                       (SELECT min(ec.report_date)
                          FROM earnings_calendar ec
                         WHERE ec.ticker = p.ticker
                           AND ec.report_date >= CURRENT_DATE) AS er_date,
                       (SELECT min(ec.report_date) - CURRENT_DATE
                          FROM earnings_calendar ec
                         WHERE ec.ticker = p.ticker
                           AND ec.report_date >= CURRENT_DATE) AS er_days,
                       (SELECT string_agg(b.pattern || ' ' || b.timeframe
                                          || ' (' || b.status || ')', ' + ')
                          FROM pattern_scan b
                         WHERE b.ticker = p.ticker
                           AND b.direction = 'bearish'
                           AND b.status IN ('forming','retest','breakout')
                       ) AS bear_live
                FROM pattern_scan p
                LEFT JOIN screener_snapshot s ON s.ticker = p.ticker
                LEFT JOIN oscillator_scan w
                       ON w.ticker = p.ticker AND w.timeframe = 'weekly'
                WHERE (%(tf)s = 'all' OR p.timeframe = %(tf)s)
                  AND (%(st)s = 'all' OR p.status = %(st)s)
                  AND (%(dir)s = 'all' OR p.direction = %(dir)s)
                  AND (%(pat)s = 'ALL' OR p.pattern = %(pat)s)
                  AND (%(q)s = '' OR p.ticker LIKE %(q)s || '%%')
                ORDER BY p.score DESC NULLS LAST, p.ticker
                LIMIT 800
                """,
                {"tf": tf, "st": status, "dir": direction, "pat": pattern,
                 "q": search},
            )
            rows = cur.fetchall()
            cur.execute("""
                SELECT timeframe, status, count(*), max(scanned_at)
                FROM pattern_scan GROUP BY timeframe, status
            """)
            agg = cur.fetchall()
            # True per-pattern counts under the tf/status/direction filter
            # (but NOT the pattern filter) — feeds the dropdown so every
            # pattern stays selectable with its real count.
            cur.execute(
                """
                SELECT pattern, count(*)
                FROM pattern_scan
                WHERE (%(tf)s = 'all' OR timeframe = %(tf)s)
                  AND (%(st)s = 'all' OR status = %(st)s)
                  AND (%(dir)s = 'all' OR direction = %(dir)s)
                GROUP BY pattern
                """,
                {"tf": tf, "st": status, "dir": direction},
            )
            pat_agg = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    def _f(v):
        return float(v) if v is not None else None
    try:
        from analysis.pattern_backtest import estimate_resolution
        timing = _pattern_timing_cached()
    except Exception:
        timing, estimate_resolution = {}, None
    out = []
    for r in rows:
        row = {
            "ticker": r[0], "timeframe": r[1], "pattern": r[2], "direction": r[3],
            "status": r[4], "trigger": _f(r[5]), "target": _f(r[6]),
            "invalid": _f(r[7]), "last_close": _f(r[8]), "dist_pct": _f(r[9]),
            "score": _f(r[10]), "points": r[11] or {},
            "anchor_date": r[12].isoformat() if r[12] else None,
            "detected_at": r[13].isoformat() if r[13] else None,
            "company_name": r[15] or "", "sector": r[16] or "",
            "rs_pct": int(r[17]) if r[17] is not None else None,
            "weekly_dir": r[18],
            "er_date": r[19].isoformat() if r[19] is not None else None,
            "er_days": int(r[20]) if r[20] is not None else None,
            # CIFR/MNDY doctrine, extended to this surface (2026-08-24,
            # the CMS case): a bullish row must carry its live bearish
            # siblings — a pretty weekly setup beside an unseen daily
            # breakdown reads as a buy. Warning only, never a gate.
            "bear_live": r[21] if r[3] == "bullish" else None,
        }
        if estimate_resolution is not None:
            row["est"] = estimate_resolution(r[2], r[1], r[12], timing)
        out.append(row)
    counts = {}
    as_of = None
    for tf_k, st_k, n, ts in agg:
        counts[f"{tf_k}:{st_k}"] = int(n)
        if ts is not None and (as_of is None or ts > as_of):
            as_of = ts
    return {"rows": out, "count": len(out), "counts": counts,
            "pattern_counts": {p_k: int(n) for p_k, n in pat_agg},
            "as_of": as_of.isoformat() if as_of else None}


_MOMENTUM_SCANNERS = ("gappers", "pillars", "continuation", "earnings_gap",
                      "all")


def _momentum_rows(scanner: str = "gappers") -> dict:
    """Momentum scanner rows (momentum_scan) with the Watchtower column no
    momentum scanner has: the name's live swing structure joined in."""
    scanner = scanner if scanner in _MOMENTUM_SCANNERS else "gappers"
    where = {
        "gappers": "abs(m.gap_pct) >= 4",
        "pillars": "m.pillar_count >= 3",
        "continuation": "m.move_2w_pct >= 30",
        "earnings_gap": "m.earnings_gap AND abs(m.gap_pct) >= 4",
        "all": "TRUE",
    }[scanner]
    order = {
        "gappers": "abs(m.gap_pct) DESC",
        "pillars": "m.pillar_count DESC, m.day_change_pct DESC",
        "continuation": "m.move_2w_pct DESC",
        "earnings_gap": "abs(m.gap_pct) DESC",
        "all": "abs(m.day_change_pct) DESC",
    }[scanner]
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT m.ticker, m.price, m.day_change_pct, m.gap_pct,
                       m.volume, m.relvol,
                       COALESCE(m.float_shares, ts.float_shares) AS float_shares,
                       m.short_interest,
                       m.news_flag, m.headline, m.pillars, m.pillar_count,
                       m.move_2w_pct, m.former_momo, m.earnings_gap,
                       m.session, m.scanned_at,
                       p.pattern, p.timeframe, p.status, p.dist_to_trigger_pct,
                       o.direction, o.confluence_score
                FROM momentum_scan m
                -- Floats read live from ticker_stats: the per-symbol float
                -- top-up runs AFTER the scan row is written, so the frozen
                -- scan-time value can lag a pass behind (or a whole weekend,
                -- when no further passes run to pick it up).
                LEFT JOIN ticker_stats ts ON ts.ticker = m.ticker
                LEFT JOIN LATERAL (
                    SELECT pattern, timeframe, status, dist_to_trigger_pct
                    FROM pattern_scan
                    WHERE ticker = m.ticker
                    ORDER BY score DESC NULLS LAST LIMIT 1) p ON true
                LEFT JOIN oscillator_scan o
                       ON o.ticker = m.ticker AND o.timeframe = 'daily'
                WHERE {where}
                ORDER BY {order}
                LIMIT 250
            """)
            rows = cur.fetchall()
            cur.execute("SELECT max(scanned_at), max(session) FROM momentum_scan")
            as_of, session = cur.fetchone() or (None, None)
    finally:
        conn.close()

    def _f(v):
        return float(v) if v is not None else None
    out = []
    for r in rows:
        out.append({
            "ticker": r[0], "price": _f(r[1]), "chg_pct": _f(r[2]),
            "gap_pct": _f(r[3]), "volume": int(r[4]) if r[4] else 0,
            "relvol": _f(r[5]),
            "float_shares": int(r[6]) if r[6] else None,
            "short_interest": int(r[7]) if r[7] else None,
            "news": bool(r[8]), "headline": r[9] or "",
            "pillars": r[10] or {}, "pillar_count": int(r[11] or 0),
            "move_2w_pct": _f(r[12]), "former_momo": bool(r[13]),
            "earnings_gap": bool(r[14]), "session": r[15],
            "pattern": r[17], "pattern_tf": r[18], "pattern_status": r[19],
            "pattern_dist": _f(r[20]),
            "osc_dir": r[21],
            "osc_conf": int(r[22]) if r[22] is not None else None,
        })
    return {"rows": out, "count": len(out), "session": session,
            "as_of": as_of.isoformat() if as_of else None}


_OSC_SETUPS = ("entry_grade", "high_confluence", "loaded_spring",
               "cipher_reversal", "pctr_hl", "base_turn",
               "wt_extreme_cross", "pctr_hook", "divergence", "mf_round",
               "mf_curl", "any_signal")


def _oscillator_rows(tf: str = "daily", direction: str = "bullish",
                     setup: str = "entry_grade") -> dict:
    """Oscillator fleet-scan rows (oscillator_scan, migrations 0065/0066)
    filtered to a setup, with the screener's sector/RS context and the
    weekly oscillator direction joined in.

    entry_grade (the default) is the quality-gated ENTRY list: a supportive
    oscillator (confluence >= 35) is necessary but not sufficient — the name
    must also have a live pattern in the trade direction within striking
    distance of its trigger, the WEEKLY must not be actively against the
    trade (no opposing weekly oscillator direction, no opposing weekly
    pattern), and relative strength can't be bottom-of-the-barrel. Without
    those gates a pure confluence sort surfaces the most washed-out names
    in the market — knife-catches, not entries. Those still have a home:
    the other setups (high_confluence = the raw washout watchlist)."""
    tf = tf if tf in ("daily", "weekly", "4h", "1h") else "daily"
    direction = direction if direction in ("bullish", "bearish") else "all"
    setup = setup if setup in _OSC_SETUPS else "entry_grade"
    sig_dir = "up" if direction == "bullish" else "down"

    base_select = """
        SELECT o.ticker, o.timeframe, o.bar_ts, o.wt1, o.wt2, o.wt_diff,
               o.mf_candle, o.rsi, o.pctr, o.macd_hist,
               o.signals, o.confluence_score, o.direction,
               s.company_name, s.sector, s.rs_pct, s.price,
               w.direction AS wk_dir, s.vs_sma, o.bars_since_cross
    """

    if setup == "entry_grade":
        query = base_select + """
             , p.pattern, p.timeframe, p.status, p.dist_to_trigger_pct,
               (o.confluence_score
                + CASE WHEN o.direction = 'bullish'
                       THEN LEAST(GREATEST(COALESCE(s.rs_pct,0) - 25, 0), 40)
                       ELSE LEAST(GREATEST(75 - COALESCE(s.rs_pct,100), 0), 40)
                  END * 0.5
                + CASE WHEN o.direction = 'bullish'
                       THEN GREATEST(12 + LEAST(p.dist_to_trigger_pct, 0), 0)
                       ELSE GREATEST(12 - GREATEST(p.dist_to_trigger_pct, 0), 0)
                  END * 2
                + CASE WHEN w.direction = o.direction THEN 10 ELSE 0 END
               ) AS entry_rank
        FROM oscillator_scan o
        LEFT JOIN oscillator_scan w ON w.ticker = o.ticker AND w.timeframe = 'weekly'
        LEFT JOIN screener_snapshot s ON s.ticker = o.ticker
        JOIN LATERAL (
            SELECT pattern, timeframe, status, dist_to_trigger_pct
            FROM pattern_scan
            WHERE ticker = o.ticker AND direction = o.direction
              AND timeframe IN (o.timeframe, 'weekly')
            ORDER BY score DESC NULLS LAST LIMIT 1) p ON true
        WHERE o.timeframe = %(tf)s
          AND o.bar_ts > clock_timestamp() - make_interval(days => %(fresh)s)
          AND (%(dir)s = 'all' OR o.direction = %(dir)s)
          AND o.direction IN ('bullish', 'bearish')
          AND o.confluence_score >= 35
          AND ((o.direction = 'bullish'
                AND COALESCE(w.direction, 'none') != 'bearish'
                AND COALESCE(s.rs_pct, 0) >= 25)
            OR (o.direction = 'bearish'
                AND COALESCE(w.direction, 'none') != 'bullish'
                AND COALESCE(s.rs_pct, 100) <= 75))
          AND CASE WHEN o.direction = 'bullish'
                   THEN p.dist_to_trigger_pct > -12
                   ELSE p.dist_to_trigger_pct < 12 END
          AND NOT EXISTS (
              SELECT 1 FROM pattern_scan pb
              WHERE pb.ticker = o.ticker AND pb.timeframe = 'weekly'
                AND pb.direction = CASE WHEN o.direction = 'bullish'
                                        THEN 'bearish' ELSE 'bullish' END)
        ORDER BY entry_rank DESC, o.ticker
        LIMIT 400
        """
    else:
        setup_sql = {
            "high_confluence": "o.confluence_score >= 60",
            "loaded_spring": "o.signals ? 'loaded_spring'",
            "cipher_reversal": "o.signals ? 'cipher_reversal'",
            "pctr_hl": "o.signals ? 'pctr_hl'",
            "base_turn": "o.signals ? 'base_turn'",
            "wt_extreme_cross": "o.signals->'wt_cross'->>'zone' = 'extreme'"
                                + ("" if direction == "all" else
                                   f" AND o.signals->'wt_cross'->>'dir' = '{sig_dir}'"),
            "pctr_hook": "o.signals ? 'pctr_hook'",
            "divergence": "o.signals ? 'divergence'"
                          + ("" if direction == "all" else
                             f" AND o.signals->'divergence'->>'dir' = '{direction}'"),
            "mf_round": "o.signals ? 'mf_round'"
                        + ("" if direction == "all" else
                           f" AND o.signals->'mf_round'->>'dir' = '{sig_dir}'"),
            "mf_curl": "o.signals->'mf_curl'->>'volume_backed' = 'true'",
            "any_signal": "o.signals != '{}'::jsonb",
        }[setup]
        # Loaded spring, cipher reversal, and the %R higher-low family are
        # bullish by construction, but the row's COMPUTED direction is
        # usually bearish (the wash IS the setup) — filtering on it would
        # hide exactly the names these screens exist to find. Spring ranks
        # by how firmly RSI is holding; cipher reversal ranks rounded
        # arcs, then the full stack, then the deepest wash; pctr_hl ranks
        # divergent pairs off the deepest first floor; base_turn ranks by
        # relative strength (the SNAP look is a quality screen).
        _BULL_BY_CONSTRUCTION = ("loaded_spring", "cipher_reversal",
                                 "pctr_hl", "base_turn")
        dir_clause = ("" if setup in _BULL_BY_CONSTRUCTION else
                      "AND (%(dir)s = 'all' OR o.direction = %(dir)s)")
        order_sql = {
            "loaded_spring": "(o.signals->'loaded_spring'->>'rsi')::float DESC",
            "cipher_reversal":
                "COALESCE((o.signals->'cipher_reversal'->>'rounded')::boolean, false) DESC, "
                "(o.signals->'cipher_reversal'->>'full_stack')::boolean DESC, "
                "(o.signals->'cipher_reversal'->>'mf_trough')::float ASC",
            "pctr_hl":
                "COALESCE((o.signals->'pctr_hl'->>'shallow')::boolean, false) ASC, "
                "(o.signals->'pctr_hl'->>'price_div')::boolean DESC, "
                "(o.signals->'pctr_hl'->>'low1')::float ASC",
            "base_turn": "s.rs_pct DESC NULLS LAST",
        }.get(setup, "o.confluence_score DESC NULLS LAST")
        # Structural context on every row (the MNDY lesson, 2026-08-15: a
        # panel that looks bullish at a rejected trigger must say so) —
        # best-scored live pattern in ANY direction, bearish ones flagged
        # by the renderers.
        query = base_select + f"""
             , p.pattern, p.timeframe, p.status, p.dist_to_trigger_pct,
               p.direction
        FROM oscillator_scan o
        LEFT JOIN oscillator_scan w ON w.ticker = o.ticker AND w.timeframe = 'weekly'
        LEFT JOIN screener_snapshot s ON s.ticker = o.ticker
        LEFT JOIN LATERAL (
            SELECT pattern, timeframe, status, dist_to_trigger_pct, direction
            FROM pattern_scan
            WHERE ticker = o.ticker AND timeframe IN (o.timeframe, 'daily', 'weekly')
            ORDER BY score DESC NULLS LAST LIMIT 1) p ON true
        WHERE o.timeframe = %(tf)s
          AND o.bar_ts > clock_timestamp() - make_interval(days => %(fresh)s)
          {dir_clause}
          AND ({setup_sql})
        ORDER BY {order_sql}, o.ticker
        LIMIT 400
        """

    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            # Freshness gate: a stale intraday series (truncated Polygon
            # response, or a name that fell out of the candidate set) must
            # never surface as a current pick. An hourly reversal state has
            # a shelf life of hours — the 2026-08-14 lesson was ADT's
            # Tuesday 1h bar rendering as Friday's state under a 4-day gate.
            fresh_days = {"1h": 1, "4h": 2}.get(tf, 30)
            cur.execute(query, {"tf": tf, "dir": direction, "fresh": fresh_days})
            rows = cur.fetchall()
            cur.execute("SELECT max(scanned_at) FROM oscillator_scan "
                        "WHERE timeframe = %s", (tf,))
            as_of = cur.fetchone()[0]
    finally:
        try:
            conn.close()
        except Exception:
            pass

    def _f(v):
        return float(v) if v is not None else None
    out = []
    for r in rows:
        sig = r[10] or {}
        names = []
        for k, v in sig.items():
            d = v.get("dir") if isinstance(v, dict) else None
            tag = k + (f" {d}" if d else "")
            if isinstance(v, dict) and v.get("zone") == "extreme":
                tag += " (extreme)"
            if isinstance(v, dict) and v.get("volume_backed"):
                tag += " (vol)"
            if isinstance(v, dict) and v.get("count") and v.get("indicators"):
                tag += f" ({v['count']}/4: {'+'.join(v['indicators'])})"
            names.append(tag)
        row = {
            "ticker": r[0], "timeframe": r[1],
            "bar_ts": r[2].isoformat() if r[2] else None,
            "wt1": _f(r[3]), "wt2": _f(r[4]), "wt_diff": _f(r[5]),
            "mf": _f(r[6]), "rsi": _f(r[7]), "pctr": _f(r[8]),
            "macd_hist": _f(r[9]), "signals": sig, "signal_names": names,
            "confluence_score": _f(r[11]) or 0, "direction": r[12],
            "company_name": r[13] or "", "sector": r[14] or "",
            "rs_pct": int(r[15]) if r[15] is not None else None,
            "close": _f(r[16]),
            "weekly_dir": r[17],
            # float() before round — the raw DB Decimal survives round() and
            # then blows up JSON serialization AFTER the route's error guard
            "vs_sma_pct": round(float(r[18]) * 100, 1) if r[18] is not None else None,
            "bars_since_cross": int(r[19]) if r[19] is not None else None,
        }
        if setup == "entry_grade":
            row.update({
                "pattern": r[20], "pattern_tf": r[21], "pattern_status": r[22],
                "pattern_dist": _f(r[23]),
                "entry_rank": round(_f(r[24]) or 0),
            })
        elif len(r) > 20:
            # Structural context for every other setup (the MNDY lesson):
            # best live pattern in ANY direction; bearish renders flagged.
            row.update({
                "pattern": r[20], "pattern_tf": r[21], "pattern_status": r[22],
                "pattern_dist": _f(r[23]), "pattern_dir": r[24],
            })
        out.append(row)
    as_of_et = None
    if as_of is not None:
        try:
            from zoneinfo import ZoneInfo
            as_of_et = as_of.astimezone(ZoneInfo("America/New_York")) \
                .strftime("%b %d, %I:%M %p ET").replace(" 0", " ")
        except Exception:
            as_of_et = as_of.isoformat()
    return {"rows": out, "count": len(out),
            "as_of": as_of.isoformat() if as_of else None, "as_of_et": as_of_et}


def _fmp_description(ticker: str) -> str:
    """One-shot FMP /profile fetch for a company's business description."""
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    if not api_key:
        return ""
    try:
        import requests
        resp = requests.get(f"{_FMP_BASE}/profile",
                            params={"symbol": ticker, "apikey": api_key}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return (data[0].get("description") or "").strip()
    except Exception as e:
        log.warning(f"[dashboard.api] _fmp_description({ticker}) failed: {e}")
    return ""


def register_routes(mcp) -> None:
    """Attach all dashboard routes to the FastMCP instance. Must be called
    before mcp.streamable_http_app() builds the Starlette app."""

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard_page(request: Request):
        path = os.path.join(_STATIC_DIR, "index.html")
        try:
            with open(path, encoding="utf-8") as f:
                # no-cache: heuristic browser caching kept serving stale JS
                # after deploys — fixes looked broken until a hard refresh.
                return HTMLResponse(f.read(),
                                    headers={"Cache-Control": "no-cache"})
        except OSError as e:
            return HTMLResponse(f"<h1>Dashboard asset missing</h1><p>{e}</p>", status_code=500)

    @mcp.custom_route("/dashboard/desk", methods=["GET"])
    async def desk_floor_page(request: Request):
        # The Desk Floor (2026-08-23, Eric: "aesthetics would be a nice
        # thing to have"): a trading-floor view of the system's REAL
        # jobs. Every number on it comes from the ledger and the
        # ingestion log — cosmetics over record, never over vibes.
        path = os.path.join(_STATIC_DIR, "desk.html")
        try:
            with open(path, encoding="utf-8") as f:
                return HTMLResponse(f.read(),
                                    headers={"Cache-Control": "no-cache"})
        except OSError as e:
            return HTMLResponse(f"<h1>Desk asset missing</h1><p>{e}</p>",
                                status_code=500)

    @mcp.custom_route("/api/deskfloor", methods=["GET"])
    async def deskfloor(request: Request):
        if not _is_authed(request):
            return JSONResponse({"error": "auth"}, status_code=401)

        def _load():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                out = {}
                with conn.cursor() as c:
                    # Seats: freshest run per job family, from the log.
                    c.execute("""
                        SELECT DISTINCT ON (job_name) job_name, status,
                               completed_at, records_processed
                        FROM ingestion_log
                        WHERE started_at > now() - interval '3 days'
                        ORDER BY job_name, started_at DESC""")
                    out["jobs"] = [
                        {"job": r[0], "status": r[1],
                         "at": r[2].isoformat() if r[2] else None,
                         "n": r[3]} for r in c.fetchall()]
                    # Books: the ledger's own record.
                    c.execute("""
                        SELECT s.book,
                               count(*) FILTER (WHERE t.exited_at IS NULL) AS open,
                               count(*) FILTER (WHERE t.exited_at IS NOT NULL) AS resolved,
                               count(*) FILTER (WHERE t.r_multiple > 0) AS wins,
                               round(coalesce(sum(t.r_multiple)
                                     FILTER (WHERE t.exited_at IS NOT NULL), 0), 2) AS realized_r,
                               count(*) FILTER (WHERE t.entered_at::date = CURRENT_DATE) AS fills_today
                        FROM paper_trades t JOIN paper_specs s ON s.id = t.spec_id
                        GROUP BY s.book ORDER BY s.book""")
                    out["books"] = [
                        {"book": r[0], "open": r[1], "resolved": r[2],
                         "wins": r[3], "realized_r": float(r[4]),
                         "fills_today": r[5]} for r in c.fetchall()]
                    # Specs today (armed/skips per book) — zero is data.
                    c.execute("""
                        SELECT book, status, count(*) FROM paper_specs
                        WHERE trade_date = CURRENT_DATE
                        GROUP BY book, status ORDER BY book, status""")
                    out["specs_today"] = [
                        {"book": r[0], "status": r[1], "n": r[2]}
                        for r in c.fetchall()]
                    # FLOW seat: latest board per index venue.
                    c.execute("""
                        SELECT DISTINCT ON (ticker) ticker, spot, call_wall,
                               put_wall, gamma_flip, net_gex, regime
                        FROM gex_levels WHERE ticker IN ('SPY','QQQ','IWM')
                        ORDER BY ticker, computed_at DESC""")
                    out["gamma"] = [
                        {"ticker": r[0], "spot": float(r[1] or 0),
                         "cw": float(r[2] or 0), "pw": float(r[3] or 0),
                         "flip": float(r[4] or 0), "gex": float(r[5] or 0),
                         "regime": r[6]} for r in c.fetchall()]
                    # Drift chatter today (sent vs suppressed).
                    c.execute("""
                        SELECT count(*) FILTER (WHERE alerted),
                               count(*) FILTER (WHERE NOT alerted)
                        FROM gamma_drift_alerts
                        WHERE trade_date = CURRENT_DATE""")
                    r = c.fetchone()
                    out["drift"] = {"sent": r[0] or 0, "suppressed": r[1] or 0}
                    # MACRO seat: next high-impact US events.
                    c.execute("""
                        SELECT event, event_date FROM economic_calendar
                        WHERE country='US' AND impact='High'
                          AND event_date >= CURRENT_DATE
                        ORDER BY event_date LIMIT 3""")
                    out["macro"] = [
                        {"event": r[0], "date": r[1].isoformat()}
                        for r in c.fetchall()]
                    # Squawk: latest fills/exits from the ledger.
                    c.execute("""
                        SELECT s.book, s.ticker,
                               t.entered_at, t.entry_px,
                               t.exited_at, t.exit_px, t.exit_reason,
                               t.r_multiple
                        FROM paper_trades t
                        JOIN paper_specs s ON s.id = t.spec_id
                        ORDER BY greatest(t.entered_at,
                                 coalesce(t.exited_at, t.entered_at)) DESC
                        LIMIT 12""")
                    out["squawk"] = [
                        {"book": r[0], "ticker": r[1],
                         "entered_at": r[2].isoformat() if r[2] else None,
                         "entry_px": float(r[3]) if r[3] is not None else None,
                         "exited_at": r[4].isoformat() if r[4] else None,
                         "exit_px": float(r[5]) if r[5] is not None else None,
                         "exit_reason": r[6],
                         "r": float(r[7]) if r[7] is not None else None}
                        for r in c.fetchall()]
                return out
            finally:
                conn.close()

        try:
            data = await asyncio.to_thread(_load)
            return JSONResponse(data)
        except Exception as e:
            log.warning(f"[dashboard] deskfloor failed: {e}")
            return JSONResponse({"error": str(e)[:300]}, status_code=500)

    @mcp.custom_route("/api/login", methods=["POST"])
    async def login(request: Request):
        ip = _client_ip(request)
        wait = _login_throttled(ip)
        if wait:
            return JSONResponse(
                {"error": f"too many attempts — try again in {wait}s"},
                status_code=429)
        try:
            body = await request.json()
        except Exception:
            body = {}
        username = (body.get("username") or "").strip().lower()
        password = (body.get("password") or "").strip()
        if not _dashboard_password():
            return JSONResponse({"ok": True, "note": "no password configured"})

        user = await asyncio.to_thread(_get_user, username) if username else None
        if user and _verify_password(password, user["password_hash"]):
            _login_succeeded(ip)
            resp = JSONResponse({"ok": True, "username": user["username"],
                                 "role": user["role"]})
            resp.set_cookie(
                _COOKIE_NAME, _session_token(user["username"]),
                max_age=_SESSION_TTL_SEC, httponly=True, samesite="lax",
                secure=request.url.scheme == "https",
            )
            return resp

        # Legacy fallback: if the users table is missing/empty (pre-migration
        # deploy, DB hiccup) the old shared password still opens the door as
        # the owner — never lock yourself out of your own dashboard.
        if not user and hmac.compare_digest(password, _dashboard_password()):
            _login_succeeded(ip)
            resp = JSONResponse({"ok": True, "username": "eric", "role": "owner",
                                 "note": "legacy password login"})
            resp.set_cookie(
                _COOKIE_NAME, _session_token("eric"),
                max_age=_SESSION_TTL_SEC, httponly=True, samesite="lax",
                secure=request.url.scheme == "https",
            )
            return resp

        _login_failed(ip)
        return JSONResponse({"error": "wrong username or password"}, status_code=401)

    @mcp.custom_route("/api/me", methods=["GET"])
    async def me(request: Request):
        u = _current_user(request)
        if not u:
            return _unauthorized()
        user = await asyncio.to_thread(_get_user, u)
        return JSONResponse({"username": u,
                             "role": (user or {}).get("role", "owner"),
                             "display_name": (user or {}).get("display_name", u)})

    @mcp.custom_route("/api/logout", methods=["POST"])
    async def logout(request: Request):
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(_COOKIE_NAME)
        return resp

    @mcp.custom_route("/api/change-password", methods=["POST"])
    async def change_password(request: Request):
        u = _current_user(request)
        if not u:
            return _unauthorized()
        try:
            body = await request.json()
        except Exception:
            body = {}
        old, new = (body.get("old") or "").strip(), (body.get("new") or "").strip()
        if len(new) < 8:
            return JSONResponse({"error": "new password must be at least 8 characters"},
                                status_code=400)
        user = await asyncio.to_thread(_get_user, u)
        if not user or not _verify_password(old, user["password_hash"]):
            return JSONResponse({"error": "current password is wrong"}, status_code=401)

        def _set():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE dashboard_users SET password_hash=%s, "
                                "updated_at=now() WHERE username=%s",
                                (_hash_password(new), u))
                conn.commit()
            finally:
                conn.close()
        await asyncio.to_thread(_set)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/users", methods=["GET", "POST"])
    async def users_admin(request: Request):
        """Owner-only user management. Owner rows are additionally protected by
        DB triggers (migration 0060): a manager can never deactivate, demote,
        or delete the owner by ANY path."""
        u = _current_user(request)
        if not u:
            return _unauthorized()
        user = await asyncio.to_thread(_get_user, u)
        if not user or user["role"] != "owner":
            return JSONResponse({"error": "owner only"}, status_code=403)

        if request.method == "GET":
            def _list():
                from screen.reversal_screen import _conn
                conn = _conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT username, role, display_name, active, "
                                    "created_at FROM dashboard_users ORDER BY role, username")
                        return [{"username": r[0], "role": r[1], "display_name": r[2],
                                 "active": r[3],
                                 "created_at": r[4].isoformat() if r[4] else None}
                                for r in cur.fetchall()]
                finally:
                    conn.close()
            return JSONResponse({"users": await asyncio.to_thread(_list)})

        try:
            body = await request.json()
        except Exception:
            body = {}
        action = (body.get("action") or "").strip()
        target = (body.get("username") or "").strip().lower()
        import re as _re
        if not _re.match(r"^[a-z0-9_]{2,24}$", target or ""):
            return JSONResponse({"error": "invalid username"}, status_code=400)

        def _admin():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    if action == "create":
                        pw = (body.get("password") or "").strip()
                        if len(pw) < 8:
                            return {"error": "password must be at least 8 characters"}
                        cur.execute(
                            "INSERT INTO dashboard_users (username, password_hash, role, display_name) "
                            "VALUES (%s, %s, 'manager', %s) ON CONFLICT (username) DO NOTHING",
                            (target, _hash_password(pw),
                             (body.get("display_name") or target)[:40]))
                        if cur.rowcount == 0:
                            return {"error": "username already exists"}
                    elif action == "reset_password":
                        pw = (body.get("password") or "").strip()
                        if len(pw) < 8:
                            return {"error": "password must be at least 8 characters"}
                        # owner may reset MANAGER passwords; own password goes
                        # through change-password (needs the current one)
                        cur.execute("UPDATE dashboard_users SET password_hash=%s, updated_at=now() "
                                    "WHERE username=%s AND role='manager'",
                                    (_hash_password(pw), target))
                        if cur.rowcount == 0:
                            return {"error": "no such manager account"}
                    elif action in ("deactivate", "activate"):
                        cur.execute("UPDATE dashboard_users SET active=%s, updated_at=now() "
                                    "WHERE username=%s AND role='manager'",
                                    (action == "activate", target))
                        if cur.rowcount == 0:
                            return {"error": "no such manager account"}
                    else:
                        return {"error": "unknown action"}
                conn.commit()
                return {"ok": True}
            finally:
                conn.close()
        result = await asyncio.to_thread(_admin)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @mcp.custom_route("/api/scan/latest", methods=["GET"])
    async def scan_latest(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        from dashboard import store
        snapshot = await asyncio.to_thread(store.get_latest)
        if snapshot is None:
            return JSONResponse({
                "as_of": None, "signals": [], "news": [], "market_pulse": {},
                "signal_count": 0, "scan_running": _scan_running.is_set(),
                "note": "No scan persisted yet — trigger one with the Scan Now button.",
            })
        snapshot = dict(snapshot)
        # The snapshot's market state is frozen at scan time; the header is about
        # "now", so recompute it live (calendar-aware) — otherwise on a weekend it
        # keeps showing Friday's "Market open".
        try:
            from screen.market_calendar import market_minutes
            minutes_elapsed, is_market_hours = market_minutes()
            snapshot["minutes_elapsed"] = minutes_elapsed
            snapshot["is_market_hours"] = is_market_hours
        except Exception:
            pass
        snapshot["scan_running"] = _scan_running.is_set()
        return JSONResponse(snapshot)

    @mcp.custom_route("/api/scan/run", methods=["POST"])
    async def scan_run(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        if _scan_running.is_set():
            return JSONResponse({"ok": False, "error": "scan already running"}, status_code=409)

        def _run():
            _scan_running.set()
            try:
                from alerts.scheduler import run_scheduled_scan
                run_scheduled_scan(force=True)  # manual trigger bypasses the market-closed gate
            except Exception as e:
                log.error(f"[dashboard.api] Manual scan failed: {e}")
            finally:
                _scan_running.clear()

        threading.Thread(target=_run, daemon=True, name="dashboard-manual-scan").start()
        return JSONResponse({"ok": True, "status": "scan started"})

    @mcp.custom_route("/api/ticker/{ticker}", methods=["GET"])
    async def ticker_detail(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        ticker = (request.path_params.get("ticker") or "").upper().strip()
        if not ticker or len(ticker) > 6 or not ticker.replace(".", "").isalnum():
            return JSONResponse({"error": "invalid ticker"}, status_code=400)

        def _fetch():
            # The drawer is the most-used click in the app and these five
            # blocks used to run serially (intraday -> levels' six Polygon
            # timeframe fetches -> Grok social -> profile -> fair value),
            # taking many seconds. They're independent except that levels and
            # fair-value want the live price from intraday — so intraday runs
            # first, then the other four fan out on a small thread pool.
            from concurrent.futures import ThreadPoolExecutor
            out = {"ticker": ticker, "intraday": None, "social": None, "levels": None}
            price = None
            try:
                from screen.intraday_screen import run_screen as run_intraday
                rows = run_intraday(min_score=0.0, single_ticker=ticker)
                if rows and not rows[0].get("error"):
                    out["intraday"] = rows[0]
                    price = rows[0].get("current_price")
            except Exception as e:
                out["intraday_error"] = str(e)[:120]

            def _levels():
                from analysis.levels import compute_levels
                return "levels", compute_levels(ticker, current_price=price)

            def _social():
                from analysis.social_buzz import query_ticker_sentiment
                return "social", query_ticker_sentiment(ticker)

            def _profile():
                return "profile", _company_profile(ticker)

            def _fair():
                from analysis.fundamental_value import compute_fair_value, fundamentals_snapshot
                return "fair", (compute_fair_value(ticker, price_override=price),
                                fundamentals_snapshot(ticker))

            def _member():
                return "memberships", _ticker_memberships(ticker)

            def _gamma():
                from screen.reversal_screen import _conn
                conn = _conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT as_of, spot, call_wall, put_wall,
                                   gamma_flip, net_gex, regime
                            FROM gex_levels WHERE ticker = %s
                            ORDER BY as_of DESC LIMIT 1
                        """, (ticker,))
                        r = cur.fetchone()
                finally:
                    conn.close()
                if not r:
                    return "gamma", None
                return "gamma", {
                    "as_of": r[0].isoformat(),
                    "spot": float(r[1]) if r[1] is not None else None,
                    "call_wall": float(r[2]) if r[2] is not None else None,
                    "put_wall": float(r[3]) if r[3] is not None else None,
                    "gamma_flip": float(r[4]) if r[4] is not None else None,
                    "net_gex": float(r[5]) if r[5] is not None else None,
                    "regime": r[6]}

            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(fn): name for fn, name in
                           ((_levels, "levels"), (_social, "social"),
                            (_profile, "profile"), (_fair, "fair_value"),
                            (_member, "memberships"), (_gamma, "gamma"))}
                for fut, name in futures.items():
                    try:
                        key, val = fut.result(timeout=45)
                        if key == "fair":
                            out["fair_value"], out["fundamentals"] = val
                        else:
                            out[key] = val
                    except Exception as e:
                        out[f"{name}_error"] = str(e)[:120]
            return out

        return JSONResponse(await asyncio.to_thread(_fetch))

    @mcp.custom_route("/api/performance", methods=["GET"])
    async def performance(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            days = max(1, min(365, int(request.query_params.get("days", "90"))))
        except ValueError:
            days = 90
        alert_type = request.query_params.get("type") or None

        def _fetch():
            from analysis.alert_tracker import get_performance_report
            return get_performance_report(days_back=days, alert_type=alert_type)

        report = await asyncio.to_thread(_fetch)
        return JSONResponse(report)

    @mcp.custom_route("/api/swing/latest", methods=["GET"])
    async def swing_latest(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            data = await asyncio.to_thread(_swing_rows)
        except Exception as e:
            return JSONResponse({"as_of": None, "rows": [], "count": 0, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/gems/latest", methods=["GET"])
    async def gems_latest(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            data = await asyncio.to_thread(_gems_rows)
        except Exception as e:
            return JSONResponse({"as_of": None, "gems": [], "heat": [], "count": 0, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/heatmap", methods=["GET"])
    async def heatmap(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        tf = (request.query_params.get("tf") or "quarterly").lower()
        if tf not in _HEAT_WINDOWS:
            tf = "quarterly"
        days = _HEAT_WINDOWS[tf]
        weight = (request.query_params.get("weight") or "median").lower()
        if weight not in ("median", "cap"):
            weight = "median"
        try:
            sectors = await asyncio.to_thread(_sector_heat_live, tf, weight)
        except Exception as e:
            return JSONResponse({"tf": tf, "window_days": days, "weight": weight, "sectors": [], "error": str(e)[:120]})
        return JSONResponse({"tf": tf, "window_days": days, "weight": weight, "sectors": sectors})

    @mcp.custom_route("/api/heatmap-etf", methods=["GET"])
    async def heatmap_etf(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        tf = (request.query_params.get("tf") or "monthly").lower()
        if tf not in _HEAT_WINDOWS:
            tf = "monthly"
        days = _HEAT_WINDOWS[tf]
        try:
            etfs = await asyncio.to_thread(_etf_heat_snapshot, tf)
        except Exception as e:
            return JSONResponse({"tf": tf, "window_days": days, "etfs": [], "error": str(e)[:120]})
        return JSONResponse({"tf": tf, "window_days": days, "etfs": etfs})

    @mcp.custom_route("/api/etf-holdings", methods=["GET"])
    async def etf_holdings(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        etf = (request.query_params.get("etf") or "").upper().strip()
        if not etf or not etf.isalnum() or len(etf) > 8:
            return JSONResponse({"etf": etf, "tf": "monthly", "holdings": [], "error": "bad etf symbol"})
        tf = (request.query_params.get("tf") or "monthly").lower()
        if tf not in _HEAT_SESSIONS:
            tf = "monthly"
        sessions = _HEAT_SESSIONS[tf]
        try:
            holdings = await asyncio.to_thread(_etf_holdings, etf, sessions)
        except Exception as e:
            return JSONResponse({"etf": etf, "tf": tf, "holdings": [], "error": str(e)[:120]})
        return JSONResponse({"etf": etf, "tf": tf, "holdings": holdings, "count": len(holdings)})

    @mcp.custom_route("/api/calendar/earnings", methods=["GET"])
    async def calendar_earnings(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            days = int(request.query_params.get("days") or 14)
        except ValueError:
            days = 14
        days = max(1, min(days, 30))
        try:
            rows = await asyncio.to_thread(_earnings_calendar_rows, days)
        except Exception as e:
            return JSONResponse({"days": days, "rows": [], "error": str(e)[:120]})
        return JSONResponse({"days": days, "rows": rows})

    @mcp.custom_route("/api/calendar/economic", methods=["GET"])
    async def calendar_economic(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            days = int(request.query_params.get("days") or 14)
        except ValueError:
            days = 14
        days = max(1, min(days, 30))
        try:
            rows = await asyncio.to_thread(_economic_calendar_rows, days)
        except Exception as e:
            return JSONResponse({"days": days, "rows": [], "error": str(e)[:120]})
        return JSONResponse({"days": days, "rows": rows})

    @mcp.custom_route("/api/rotation", methods=["GET"])
    async def rotation(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            data = await asyncio.to_thread(_rotation_rows)
        except Exception as e:
            return JSONResponse({"as_of": None, "narrative": None, "rotating_in": [],
                                 "rotating_out": [], "sectors": [], "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/early-turn", methods=["GET"])
    async def early_turn(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            data = await asyncio.to_thread(_early_turn_rows)
        except Exception as e:
            return JSONResponse({"as_of": None, "rows": [], "count": 0, "error": str(e)[:120]})
        # Bearish mirror rides the same payload: cooling industries, the
        # risk-off gauge, and the put-scouting list. Non-fatal if it fails —
        # the bullish radar still renders.
        try:
            data.update(await asyncio.to_thread(_bearish_rotation_rows))
        except Exception as e:
            log.warning(f"[api] bearish rotation error (non-fatal): {e}")
            data.update({"cooling": [], "risk": {}, "puts": []})
        return JSONResponse(data)

    @mcp.custom_route("/api/themes", methods=["GET"])
    async def themes(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        window = request.query_params.get("window") or "ytd"
        weight = request.query_params.get("weight") or "median"
        try:
            data = await asyncio.to_thread(_theme_rows, window, weight)
        except Exception as e:
            return JSONResponse({"as_of": None, "rows": [], "count": 0, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/themes/members", methods=["GET"])
    async def theme_members_route(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        theme = request.query_params.get("theme") or ""
        window = request.query_params.get("window") or "ytd"
        if not theme:
            return JSONResponse({"rows": [], "count": 0, "error": "theme required"})
        try:
            data = await asyncio.to_thread(_theme_members, theme, window)
        except Exception as e:
            return JSONResponse({"theme": theme, "rows": [], "count": 0, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/momentum", methods=["GET"])
    async def momentum(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        scanner = (request.query_params.get("scanner") or "gappers").lower()
        try:
            data = await asyncio.to_thread(_momentum_rows, scanner)
        except Exception as e:
            return JSONResponse({"rows": [], "count": 0, "as_of": None,
                                 "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/patterns", methods=["GET"])
    async def patterns(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        tf = (request.query_params.get("tf") or "all").lower()
        status = (request.query_params.get("status") or "all").lower()
        direction = (request.query_params.get("direction") or "all").lower()
        pattern = request.query_params.get("pattern") or "ALL"
        search = request.query_params.get("search") or ""
        try:
            data = await asyncio.to_thread(_pattern_rows, tf, status, direction,
                                           pattern, search)
        except Exception as e:
            return JSONResponse({"rows": [], "count": 0, "counts": {},
                                 "as_of": None, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/oscillator", methods=["GET"])
    async def oscillator(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        tf = (request.query_params.get("tf") or "daily").lower()
        direction = (request.query_params.get("direction") or "bullish").lower()
        setup = (request.query_params.get("setup") or "high_confluence").lower()
        try:
            data = await asyncio.to_thread(_oscillator_rows, tf, direction, setup)
        except Exception as e:
            return JSONResponse({"rows": [], "count": 0, "as_of": None,
                                 "as_of_et": None, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/option-ticket", methods=["GET"])
    async def option_ticket(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        ticker = (request.query_params.get("ticker") or "").upper().strip()
        if not ticker:
            return JSONResponse({"error": "ticker required"})
        try:
            from analysis.options_picker import build_ticket
            data = await asyncio.to_thread(build_ticket, ticker)
        except Exception as e:
            return JSONResponse({"error": str(e)[:120]})
        return JSONResponse(data or {})

    @mcp.custom_route("/api/screener", methods=["GET"])
    async def screener(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        sector = request.query_params.get("sector") or "ALL"
        sort = (request.query_params.get("sort") or "score").lower()
        gems_only = (request.query_params.get("gems_only") or "").lower() in ("1", "true", "yes")
        cap = (request.query_params.get("cap") or "gem").lower()
        if cap not in _SCREENER_CAPS:
            cap = "gem"
        industry = request.query_params.get("industry") or ""
        search = request.query_params.get("search") or ""
        try:
            data = await asyncio.to_thread(_screener_rows, sector, sort, gems_only, cap, industry, search)
        except Exception as e:
            return JSONResponse({"sectors": [], "rows": [], "count": 0, "total": 0, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/gem-performance", methods=["GET"])
    async def gem_performance(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            data = await asyncio.to_thread(_gem_performance)
        except Exception as e:
            return JSONResponse({"total_picks": 0, "overall": [], "by_sleeve": [], "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/gem-departures", methods=["GET"])
    async def gem_departures(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        try:
            data = await asyncio.to_thread(_gem_departures)
        except Exception as e:
            return JSONResponse({"as_of": None, "rows": [], "count": 0, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/vantage", methods=["GET"])
    async def vantage(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        metric = (request.query_params.get("metric") or "pe").lower()
        universe = request.query_params.get("universe") or "ALL"
        color = (request.query_params.get("color") or "abs").lower()
        mincap = _VANTAGE_MINCAPS.get((request.query_params.get("mincap") or "2b").lower(), 2e9)
        # A single sector rarely has >1500 valued names, so show ALL of them above
        # the chosen floor (lets small-caps like a $450M gem tile). The ALL-sectors
        # overview stays size-capped so it doesn't render thousands of tiles.
        limit = 400 if universe == "ALL" else 1500
        try:
            data = await asyncio.to_thread(_vantage_rows, metric, universe, color, mincap, limit)
        except Exception as e:
            return JSONResponse({"tiles": [], "count": 0, "error": str(e)[:160]})
        return JSONResponse(data)

    @mcp.custom_route("/api/option-chain-lite", methods=["GET"])
    async def option_chain_lite(request: Request):
        """Chain for the manual Option Projector — any ticker, no pattern."""
        if not _is_authed(request):
            return _unauthorized()
        ticker = (request.query_params.get("ticker") or "").upper().strip()
        if not ticker or len(ticker) > 6:
            return JSONResponse({"error": "invalid ticker"}, status_code=400)
        try:
            from analysis.options_picker import chain_lite
            data = await asyncio.to_thread(chain_lite, ticker)
        except Exception as e:
            data = {"error": str(e)[:120]}
        return JSONResponse(data)

    @mcp.custom_route("/api/gamma/compute", methods=["POST"])
    async def gamma_compute(request: Request):
        """On-demand gamma levels for any optionable ticker — the drawer's
        'Compute now' button for names outside the nightly sweep. Stores
        the result so the session's levels persist and the ticker shows
        instantly on the next open."""
        if not _is_authed(request):
            return _unauthorized()
        ticker = (request.query_params.get("ticker") or "").upper().strip()
        if not ticker or len(ticker) > 6:
            return JSONResponse({"error": "invalid ticker"}, status_code=400)

        def _run():
            import json as _json
            from analysis.gex import compute_gex
            from analysis.options_picker import iv_session_date
            from screen.reversal_screen import _conn
            spot = None
            try:
                from analysis.news_scanner import _fetch_snapshot_map
                spot = (_fetch_snapshot_map([ticker]).get(ticker) or {}).get("price")
            except Exception:
                pass
            conn = _conn()
            try:
                if not spot:
                    with conn.cursor() as cur:
                        cur.execute("SELECT close FROM daily_prices WHERE ticker=%s "
                                    "ORDER BY trade_date DESC LIMIT 1", (ticker,))
                        r = cur.fetchone()
                        spot = float(r[0]) if r else None
                g = compute_gex(ticker, fallback_spot=spot)
                if not g:
                    return {"error": "chain too thin for meaningful gamma "
                                     "levels (needs real open interest)"}
                as_of = iv_session_date()
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO gex_levels
                            (ticker, as_of, spot, call_wall, put_wall,
                             gamma_flip, net_gex, regime, top_strikes, contracts)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT (ticker, as_of) DO UPDATE SET
                            spot=EXCLUDED.spot, call_wall=EXCLUDED.call_wall,
                            put_wall=EXCLUDED.put_wall,
                            gamma_flip=EXCLUDED.gamma_flip,
                            net_gex=EXCLUDED.net_gex, regime=EXCLUDED.regime,
                            top_strikes=EXCLUDED.top_strikes,
                            contracts=EXCLUDED.contracts, computed_at=now()
                    """, (ticker, as_of, g["spot"], g["call_wall"],
                          g["put_wall"], g["gamma_flip"], g["net_gex_bn"],
                          g["regime"], _json.dumps(g["top_strikes"]),
                          g["contracts"]))
                conn.commit()
                return {"as_of": str(as_of), "spot": g["spot"],
                        "call_wall": g["call_wall"], "put_wall": g["put_wall"],
                        "gamma_flip": g["gamma_flip"], "net_gex": g["net_gex_bn"],
                        "regime": g["regime"]}
            finally:
                conn.close()

        try:
            data = await asyncio.to_thread(_run)
        except Exception as e:
            data = {"error": str(e)[:140]}
        return JSONResponse(data)

    @mcp.custom_route("/api/gamma", methods=["GET"])
    async def gamma(request: Request):
        """Latest dealer-gamma regime per index — feeds the pulse-bar chip."""
        if not _is_authed(request):
            return _unauthorized()

        def _rows():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (ticker) ticker, as_of, spot,
                               call_wall, put_wall, gamma_flip, net_gex, regime
                        FROM gex_levels
                        WHERE ticker = ANY(%s)
                        ORDER BY ticker, as_of DESC
                    """, (["SPY", "QQQ"],))
                    return [
                        {"ticker": r[0], "as_of": r[1].isoformat(),
                         "spot": float(r[2]) if r[2] is not None else None,
                         "call_wall": float(r[3]) if r[3] is not None else None,
                         "put_wall": float(r[4]) if r[4] is not None else None,
                         "gamma_flip": float(r[5]) if r[5] is not None else None,
                         "net_gex": float(r[6]) if r[6] is not None else None,
                         "regime": r[7]}
                        for r in cur.fetchall()
                    ]
            finally:
                conn.close()

        try:
            rows = await asyncio.to_thread(_rows)
        except Exception:
            rows = []
        return JSONResponse({"gamma": rows})

    @mcp.custom_route("/api/vix", methods=["GET"])
    async def vix(request: Request):
        """Vol regime dial for the pulse bar — level, zone, term
        structure, 1-day change. A dial, not a trigger."""
        if not _is_authed(request):
            return _unauthorized()
        try:
            from analysis.vix import get_vix_context
            data = await asyncio.to_thread(get_vix_context)
        except Exception:
            data = {}
        return JSONResponse(data or {})

    @mcp.custom_route("/api/gamma/intraday", methods=["GET"])
    async def gamma_intraday(request: Request):
        """Today's 15-minute net-GEX path for one index — the drawer's
        day-path sparkline. Empty list for names outside the intraday
        sweep (single names are nightly + on-demand only)."""
        if not _is_authed(request):
            return _unauthorized()
        ticker = (request.query_params.get("ticker") or "").upper().strip()
        if not ticker or len(ticker) > 6:
            return JSONResponse({"error": "invalid ticker"}, status_code=400)

        def _rows():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ts, spot, net_gex, gamma_flip, regime
                        FROM gex_intraday
                        WHERE ticker = %s
                          AND ts >= date_trunc('day',
                                now() AT TIME ZONE 'America/New_York')
                                AT TIME ZONE 'America/New_York'
                        ORDER BY ts
                    """, (ticker,))
                    return [
                        {"ts": r[0].isoformat(),
                         "spot": float(r[1]) if r[1] is not None else None,
                         "net_gex": float(r[2]) if r[2] is not None else None,
                         "gamma_flip": float(r[3]) if r[3] is not None else None,
                         "regime": r[4]}
                        for r in cur.fetchall()
                    ]
            finally:
                conn.close()

        try:
            rows = await asyncio.to_thread(_rows)
        except Exception:
            rows = []
        return JSONResponse({"ticker": ticker, "path": rows})

    @mcp.custom_route("/api/vantage/lookup", methods=["GET"])
    async def vantage_lookup(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        ticker = (request.query_params.get("ticker") or "").upper().strip()
        if not ticker:
            return JSONResponse({"found": False})
        try:
            data = await asyncio.to_thread(_vantage_lookup, ticker)
        except Exception as e:
            return JSONResponse({"found": False, "ticker": ticker, "error": str(e)[:120]})
        return JSONResponse(data)

    @mcp.custom_route("/api/watchlist", methods=["GET", "POST"])
    async def watchlist(request: Request):
        if not _is_authed(request):
            return _unauthorized()

        me_user = _current_user(request)

        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:
                body = {}
            ticker = (body.get("ticker") or "").upper().strip()
            notes = (body.get("notes") or "").strip()[:300]
            if not ticker or len(ticker) > 6 or not ticker.replace(".", "").isalnum():
                return JSONResponse({"error": "invalid ticker"}, status_code=400)

            def _add():
                from screen.reversal_screen import _conn
                conn = _conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO watchlist (owner, ticker, notes, active)
                            VALUES (%s, %s, %s, true)
                            ON CONFLICT (owner, ticker)
                            DO UPDATE SET active = true,
                                          notes = COALESCE(NULLIF(EXCLUDED.notes, ''), watchlist.notes)
                            """,
                            (me_user, ticker, notes),
                        )
                    conn.commit()
                finally:
                    conn.close()

            await asyncio.to_thread(_add)
            return JSONResponse({"ok": True, "ticker": ticker})

        # scope=mine (default) shows your list; scope=all shows both partners'
        # lists side by side (separate-but-visible), with rows tagged by owner.
        scope = (request.query_params.get("scope") or "mine").lower()

        def _list():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    if scope == "all":
                        cur.execute(
                            "SELECT ticker, notes, added_at, owner FROM watchlist "
                            "WHERE active = true ORDER BY added_at DESC")
                    else:
                        cur.execute(
                            "SELECT ticker, notes, added_at, owner FROM watchlist "
                            "WHERE active = true AND owner = %s ORDER BY added_at DESC",
                            (me_user,))
                    rows = [
                        {"ticker": r[0], "notes": r[1] or "",
                         "added_at": r[2].isoformat() if r[2] else None,
                         "owner": r[3], "own": r[3] == me_user}
                        for r in cur.fetchall()
                    ]
                    # Next earnings per name — a watchlist row is usually a
                    # position, and positions don't get surprised by prints.
                    if rows:
                        cur.execute("""
                            SELECT ticker, min(report_date),
                                   min(report_date) - CURRENT_DATE
                            FROM earnings_calendar
                            WHERE ticker = ANY(%s)
                              AND report_date >= CURRENT_DATE
                            GROUP BY ticker
                        """, ([r["ticker"] for r in rows],))
                        er = {t: (d.isoformat(), int(n))
                              for t, d, n in cur.fetchall()}
                        for r in rows:
                            r["er_date"], r["er_days"] = er.get(
                                r["ticker"], (None, None))
            finally:
                conn.close()
            # Live quotes so the watchlist works as a position monitor —
            # the scanner drops a ticker the moment its setup goes quiet,
            # but a name you've entered needs to stay watchable.
            if rows:
                try:
                    from analysis.news_scanner import _fetch_snapshot_map
                    snap_map = _fetch_snapshot_map([r["ticker"] for r in rows])
                    for r in rows:
                        s = snap_map.get(r["ticker"], {})
                        r["price"] = s.get("price", 0)
                        r["change_pct"] = round(s.get("change_pct", 0), 2)
                        r["vol_ratio"] = round(s.get("vol_ratio", 0), 2)
                except Exception:
                    pass
            return rows

        try:
            rows = await asyncio.to_thread(_list)
        except Exception as e:
            return JSONResponse({"error": str(e)[:120]}, status_code=500)
        return JSONResponse({"watchlist": rows})

    @mcp.custom_route("/api/watchlist/{ticker}", methods=["DELETE"])
    async def watchlist_delete(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        ticker = (request.path_params.get("ticker") or "").upper().strip()
        me_user = _current_user(request)

        def _remove():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    # own rows only — you can see your partner's list but not edit it
                    cur.execute("UPDATE watchlist SET active = false "
                                "WHERE ticker = %s AND owner = %s", (ticker, me_user))
                conn.commit()
            finally:
                conn.close()

        try:
            await asyncio.to_thread(_remove)
        except Exception as e:
            return JSONResponse({"error": str(e)[:120]}, status_code=500)
        return JSONResponse({"ok": True, "ticker": ticker})

    @mcp.custom_route("/api/fvg/{ticker}", methods=["GET"])
    async def fvg_zones(request: Request):
        """Displacement-quality fair value gaps (open + recently inverted)
        for the drawer's Imbalances section. tf: 5m | 4h | d."""
        if not _is_authed(request):
            return _unauthorized()
        ticker = (request.path_params.get("ticker") or "").upper().strip()
        tf = (request.query_params.get("tf") or "5m").lower()

        def _run():
            from analysis.polygon_data import fetch_recent_bars, fetch_session_4h_bars
            from analysis.fvg import detect_fvgs
            if tf == "5m":
                bars = fetch_recent_bars(ticker, days=3, multiplier=5,
                                         timespan="minute")
            elif tf == "4h":
                # Session-anchored (9:30/13:30 ET, RTH only) — matches what
                # charting platforms draw. Polygon's clock-anchored 4h bars
                # produced FVG zones no chart agreed with.
                bars = fetch_session_4h_bars(ticker, days=60)
            else:
                bars = fetch_recent_bars(ticker, days=200)
            return detect_fvgs(bars or [])

        try:
            gaps = await asyncio.to_thread(_run)
        except Exception as e:
            return JSONResponse({"error": str(e)[:120]}, status_code=500)
        return JSONResponse({"ticker": ticker, "tf": tf, "gaps": gaps})
