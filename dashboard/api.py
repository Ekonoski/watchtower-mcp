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

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

log = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_COOKIE_NAME = "wt_session"
_scan_running = threading.Event()


def _dashboard_password() -> str:
    return (os.environ.get("DASHBOARD_PASSWORD") or os.environ.get("MCP_AUTH_TOKEN") or "").strip()


def _session_token() -> str:
    pw = _dashboard_password()
    if not pw:
        return ""
    return hmac.new(pw.encode(), b"watchtower-dashboard-v1", hashlib.sha256).hexdigest()


def _is_authed(request: Request) -> bool:
    pw = _dashboard_password()
    if not pw:
        return True  # no password configured — open (dev mode)
    cookie = request.cookies.get(_COOKIE_NAME, "")
    return bool(cookie) and hmac.compare_digest(cookie, _session_token())


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
    return _sector_heat_compute(_HEAT_WINDOWS.get(tf, 91), weight)


def _sector_heat_compute(window_days: int = 91, weight: str = "median") -> list:
    """Live sector heat map: rank every GICS sector hottest->coldest by price
    momentum over `window_days`, across real common stocks. Colors relative to
    the spread within the chosen window, so the map is readable at any horizon
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
                    SELECT dp.ticker,
                           (array_agg(dp.close ORDER BY dp.trade_date DESC))[1] AS last_close,
                           (array_agg(dp.close ORDER BY dp.trade_date DESC)
                              FILTER (WHERE dp.trade_date <= CURRENT_DATE - %(w)s))[1] AS close_then
                    FROM daily_prices dp
                    WHERE dp.trade_date >= CURRENT_DATE - (%(w)s + 50)
                    GROUP BY dp.ticker
                ), ret AS (
                    SELECT t.sector, COALESCE(t.market_cap, 0) AS mcap,
                           p.last_close / NULLIF(p.close_then, 0) - 1 AS r
                    FROM px p JOIN tickers t ON t.ticker = p.ticker
                    WHERE t.delisted = false AND t.sector IS NOT NULL
                      AND t.industry NOT ILIKE '%%Asset Management%%'
                      AND t.company_name NOT ILIKE '%% ETF%%'
                      AND t.company_name NOT ILIKE '%% Fund%%'
                      AND COALESCE(t.market_cap, 0) >= 50000000
                      AND p.last_close >= 1.50 AND p.close_then > 0
                )
                SELECT sector, COUNT(*) n, AVG(r) avg_ret,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r) median_ret,
                       SUM(r * mcap) / NULLIF(SUM(mcap), 0) AS capwtd_ret
                FROM ret WHERE r IS NOT NULL
                GROUP BY sector HAVING COUNT(*) >= 5
                """,
                {"w": window_days},
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


def register_routes(mcp) -> None:
    """Attach all dashboard routes to the FastMCP instance. Must be called
    before mcp.streamable_http_app() builds the Starlette app."""

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard_page(request: Request):
        path = os.path.join(_STATIC_DIR, "index.html")
        try:
            with open(path, encoding="utf-8") as f:
                return HTMLResponse(f.read())
        except OSError as e:
            return HTMLResponse(f"<h1>Dashboard asset missing</h1><p>{e}</p>", status_code=500)

    @mcp.custom_route("/api/login", methods=["POST"])
    async def login(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        password = (body.get("password") or "").strip()
        if not _dashboard_password():
            return JSONResponse({"ok": True, "note": "no password configured"})
        if not hmac.compare_digest(password, _dashboard_password()):
            return JSONResponse({"error": "wrong password"}, status_code=401)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            _COOKIE_NAME, _session_token(),
            max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
            secure=request.url.scheme == "https",
        )
        return resp

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
            try:
                from analysis.levels import compute_levels
                out["levels"] = compute_levels(ticker, current_price=price)
            except Exception as e:
                out["levels_error"] = str(e)[:120]
            try:
                from analysis.social_buzz import query_ticker_sentiment
                out["social"] = query_ticker_sentiment(ticker)
            except Exception as e:
                out["social_error"] = str(e)[:120]
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

    @mcp.custom_route("/api/vantage", methods=["GET"])
    async def vantage(request: Request):
        if not _is_authed(request):
            return _unauthorized()
        metric = (request.query_params.get("metric") or "pe").lower()
        universe = request.query_params.get("universe") or "ALL"
        color = (request.query_params.get("color") or "abs").lower()
        try:
            data = await asyncio.to_thread(_vantage_rows, metric, universe, color)
        except Exception as e:
            return JSONResponse({"tiles": [], "count": 0, "error": str(e)[:160]})
        return JSONResponse(data)

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
                            INSERT INTO watchlist (ticker, notes, active)
                            VALUES (%s, %s, true)
                            ON CONFLICT (ticker)
                            DO UPDATE SET active = true,
                                          notes = COALESCE(NULLIF(EXCLUDED.notes, ''), watchlist.notes)
                            """,
                            (ticker, notes),
                        )
                    conn.commit()
                finally:
                    conn.close()

            await asyncio.to_thread(_add)
            return JSONResponse({"ok": True, "ticker": ticker})

        def _list():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT ticker, notes, added_at FROM watchlist "
                        "WHERE active = true ORDER BY added_at DESC"
                    )
                    rows = [
                        {"ticker": r[0], "notes": r[1] or "",
                         "added_at": r[2].isoformat() if r[2] else None}
                        for r in cur.fetchall()
                    ]
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

        def _remove():
            from screen.reversal_screen import _conn
            conn = _conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE watchlist SET active = false WHERE ticker = %s", (ticker,))
                conn.commit()
            finally:
                conn.close()

        try:
            await asyncio.to_thread(_remove)
        except Exception as e:
            return JSONResponse({"error": str(e)[:120]}, status_code=500)
        return JSONResponse({"ok": True, "ticker": ticker})
