"""
Dashboard HTTP API — rides on the same FastMCP/Starlette app as the MCP server.

Routes (all JSON unless noted):
  GET  /dashboard                → the dashboard UI (HTML)
  POST /api/login                → {password} → sets session cookie
  GET  /api/scan/latest          → latest persisted scan snapshot
  POST /api/scan/run             → trigger a full scan now (async, returns immediately)
  GET  /api/ticker/{ticker}      → live single-ticker intraday check + social buzz
  GET  /api/performance          → alert performance report (?days=90&type=intraday)
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
            out = {"ticker": ticker, "intraday": None, "social": None}
            try:
                from screen.intraday_screen import run_screen as run_intraday
                rows = run_intraday(min_score=0.0, single_ticker=ticker)
                if rows and not rows[0].get("error"):
                    out["intraday"] = rows[0]
            except Exception as e:
                out["intraday_error"] = str(e)[:120]
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
