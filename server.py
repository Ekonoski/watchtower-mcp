#!/usr/bin/env python
"""
Watchtower MCP Server - Official MCP SDK version

This version uses the standard mcp Python SDK (FastMCP) for maximum compatibility
with Grok, Claude, and other MCP clients.
"""

import os
import secrets
import time
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
import uvicorn

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", 8080))
# Railway sets RAILWAY_PUBLIC_DOMAIN to the service's public hostname.
# FastMCP's transport_security validates the Host header against this value.
PUBLIC_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost")

mcp = FastMCP(
    "watchtower",
    streamable_http_path="/mcp",
    host=PUBLIC_DOMAIN,
)

PUBLIC_PATHS = {"/health"}
OAUTH_PATHS = {
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/authorize",
    "/token",
}

# In-memory store for one-time auth codes: code -> (redirect_uri, expires_at)
_auth_codes: dict[str, tuple[str, float]] = {}


def _get_screens():
    """Lazy load the screen runners from the screen/ package."""
    from screen.reversal_screen import run_screen as run_reversal
    from screen.momentum_screen import run_screen as run_momentum
    from screen.breakdown_screen import run_screen as run_breakdown
    from screen.master_screen import run_screen as run_master
    from screen.insider_burst_screen import run_screen as run_insider
    from screen.volume_burst_screen import run_screen as run_volume_burst
    return run_reversal, run_momentum, run_breakdown, run_master, run_insider, run_volume_burst


@mcp.tool()
def watchtower_run_screen(
    screen: str,
    top_n: int = 5,
    with_plan: bool = True,
    with_synthesis: bool = False,
    ticker: str = "",
) -> str:
    """Run one of Watchtower's stock screens live.

    Supports:
    - reversal: beaten-down quality stocks turning up (8/13 EMA, RSI recovery, etc.)
    - momentum: strong up-and-comers
    - breakdown: bearish ideas
    - master: broad fundamental composite
    - insider: insider activity driven
    - volume_burst: unusual volume surges — breakouts and exhaustion signals

    Use ticker="AAPL" to score any single stock through the chosen screen, regardless of
    whether it's in the quality universe. Great for on-demand stock lookups.
    Use with_plan=true to include suggested trade plan (ATR stop + position size).
    Use with_synthesis=true to append a Grok AI narrative synthesizing the top results (requires XAI_API_KEY).
    """
    run_reversal, run_momentum, run_breakdown, run_master, run_insider, run_volume_burst = _get_screens()
    t = ticker.upper() if ticker else None

    if screen == "reversal":
        results = run_reversal(min_drawdown=15.0, single_ticker=t)[:top_n]
    elif screen == "momentum":
        results = run_momentum(max_pullback=12.0, single_ticker=t)[:top_n]
    elif screen == "breakdown":
        results = run_breakdown(min_breakdown=45.0, single_ticker=t)[:top_n]
    elif screen == "master":
        results = run_master(single_ticker=t)[:top_n]
    elif screen == "insider":
        results = run_insider(single_ticker=t)[:top_n]
    elif screen == "volume_burst":
        results = run_volume_burst(min_surge=1.75, single_ticker=t)[:top_n]
    else:
        return f"Unknown screen '{screen}'. Valid options: reversal, momentum, breakdown, master, insider, volume_burst"

    lines = [f"**{screen.upper()} SCREEN RESULTS** (Top {len(results)})"]
    for r in results:
        score = (
            r.get("reversal_score")
            or r.get("momentum_score")
            or r.get("breakdown_score")
            or r.get("score", "N/A")
        )
        line = f"- **{r.get('ticker')}** | {r.get('company_name', '')[:28]} | Score: {score}"
        if with_plan and r.get("plan"):
            p = r["plan"]
            line += f" | Stop: ${p.get('stop_price', 0):.2f} | Size: {p.get('position_pct', 0):.1f}%"
        lines.append(line)

    if with_synthesis:
        try:
            from analysis.grok_synthesizer import synthesize_screen_results
            narrative = synthesize_screen_results(screen, results, top_n=min(top_n, len(results)))
            if narrative:
                lines.append(f"\n**AI Analysis:**\n{narrative}")
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
def watchtower_intraday_scan(top_n: int = 10, ticker: str = "", with_synthesis: bool = False) -> str:
    """Scan for intraday setups forming right now using live Polygon data (15-min delayed on Starter tier).

    Bullish: GAP_AND_GO, INTRADAY_BREAKOUT, VWAP_BREAKOUT, FLUSH_REVERSAL, GAP_REVERSAL
    Bearish: VWAP_REJECTION, INTRADAY_BREAKDOWN, GAP_DOWN_CONFIRM, DISTRIBUTION
    Neutral: VOLUME_SURGE (unusual activity, direction unclear)

    Use ticker="ONDS" to check a specific stock intraday.
    Use with_synthesis=true for Grok AI narrative on the top setups.
    Best used during market hours (9:30 AM - 4:00 PM ET).
    """
    from screen.intraday_screen import run_screen as run_intraday
    t = ticker.upper() if ticker else None
    results = run_intraday(single_ticker=t)[:top_n]

    if not results:
        return "No intraday setups detected above threshold right now."
    if results and results[0].get("error"):
        return f"Intraday scan error: {results[0]['error']}"

    # Check market hours from first result
    is_market_hours = results[0].get("is_market_hours", True) if results else True
    minutes_elapsed = results[0].get("minutes_elapsed", 0) if results else 0
    header = "**INTRADAY SCAN** (Live Polygon — 15-min delayed)"
    if not is_market_hours:
        header += " ⚠️ Market closed — showing last session data"
    else:
        header += f" | {minutes_elapsed}min into session"

    lines = [header]
    for r in results:
        line = (f"- **{r.get('ticker')}** | {r.get('signal_type',''):<18} | Score: {r.get('score',0):.0f}"
                f" | {r.get('change_pct',0):+.1f}% | Vol: {r.get('vol_pace_ratio',0):.1f}x"
                f" | {'↑VWAP' if r.get('above_vwap') else '↓VWAP'}"
                f" | ${r.get('current_price',0):.2f}"
                f"  {r.get('rationale','')}")
        lines.append(line)

    if with_synthesis:
        try:
            from analysis.grok_synthesizer import synthesize_screen_results
            narrative = synthesize_screen_results("intraday", results, top_n=min(top_n, len(results)))
            if narrative:
                lines.append(f"\n**AI Analysis:**\n{narrative}")
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
def watchtower_analyze_ticker(ticker: str, with_synthesis: bool = True) -> str:
    """Score any single stock across all Watchtower screens and return a full multi-sleeve report.

    Works for any ticker with price history — not limited to the quality universe.
    Runs reversal, momentum, breakdown, master, and volume_burst screens on the ticker
    and returns all scores side by side so you can see exactly how it stacks up.
    """
    run_reversal, run_momentum, run_breakdown, run_master, run_insider, run_volume_burst = _get_screens()
    t = ticker.upper()

    lines = [f"**WATCHTOWER FULL ANALYSIS — {t}**"]

    from screen.intraday_screen import run_screen as run_intraday
    screen_fns = [
        ("reversal",     lambda: run_reversal(min_drawdown=0.0, single_ticker=t, show_all=True)),
        ("momentum",     lambda: run_momentum(max_pullback=100.0, single_ticker=t)),
        ("breakdown",    lambda: run_breakdown(min_breakdown=0.0, single_ticker=t)),
        ("master",       lambda: run_master(single_ticker=t)),
        ("volume_burst", lambda: run_volume_burst(min_surge=0.5, single_ticker=t)),
        ("intraday",     lambda: run_intraday(min_score=0.0, single_ticker=t)),
    ]

    best_results = []
    for screen_name, fn in screen_fns:
        try:
            res = fn()
            if res:
                r = res[0]
                score = (r.get("reversal_score") or r.get("momentum_score")
                         or r.get("breakdown_score") or r.get("score") or 0)
                signal = r.get("signal") or r.get("signal_type") or ""
                rsi = r.get("rsi")
                price = r.get("current_price")
                pct_off = r.get("pct_off_high") or r.get("pct_from_high")
                vol_surge = r.get("vol_surge")

                detail = f"  Score: {score:.0f}" if isinstance(score, float) else f"  Score: {score}"
                if signal:
                    detail += f" | Signal: {signal}"
                if rsi is not None:
                    detail += f" | RSI: {rsi:.0f}"
                if pct_off is not None:
                    detail += f" | %OffHigh: {pct_off:.1f}%"
                if vol_surge is not None:
                    detail += f" | VolSurge: {vol_surge:.2f}x"
                if price is not None:
                    detail += f" | Price: ${price:.2f}"
                lines.append(f"\n**{screen_name.upper()}**\n{detail}")
                best_results.append((screen_name, r))
            else:
                lines.append(f"\n**{screen_name.upper()}**\n  No signal / insufficient data")
        except Exception as e:
            lines.append(f"\n**{screen_name.upper()}**\n  Error: {e}")

    if with_synthesis and best_results:
        try:
            from analysis.grok_synthesizer import synthesize_screen_results
            all_rows = [r for _, r in best_results]
            narrative = synthesize_screen_results(f"full analysis of {t}", all_rows, top_n=len(all_rows))
            if narrative:
                lines.append(f"\n**AI SYNTHESIS**\n{narrative}")
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
def watchtower_get_momentum(top_n: int = 5) -> str:
    """Get current top momentum / up-and-comers from Watchtower's momentum sleeve."""
    _, run_momentum, *_ = _get_screens()  # noqa: F841
    results = run_momentum(max_pullback=12.0)[:top_n]
    lines = ["**MOMENTUM SCREEN RESULTS** (Top {})".format(len(results))]
    for r in results:
        score = r.get("momentum_score", "N/A")
        lines.append(f"- **{r.get('ticker')}** | {r.get('company_name', '')[:28]} | Score: {score}")
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_bearish_ideas(top_n: int = 5) -> str:
    """Get current top bearish / breakdown candidates from Watchtower's bearish sleeve."""
    _, _, run_breakdown, *_ = _get_screens()
    results = run_breakdown(min_breakdown=45.0)[:top_n]
    lines = ["**BEARISH / BREAKDOWN SCREEN RESULTS** (Top {})".format(len(results))]
    for r in results:
        score = r.get("breakdown_score", "N/A")
        lines.append(f"- **{r.get('ticker')}** | {r.get('company_name', '')[:28]} | Score: {score}")
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_gmmss_context() -> str:
    """Get full current context: regime + top momentum + top bearish ideas + methodology."""
    return "GMMSS context: Bull regime (see current_regime.json in repo). Use watchtower_run_screen or the individual getters for live sleeves. Full synthesis available when all keys (POLYGON, XAI) are configured on Railway."


# ── OAuth 2.0 / PKCE endpoints ────────────────────────────────────────────────

# NOTE: /.well-known endpoints intentionally omitted.
# When they exist, Grok auto-discovers OAuth and tries to run the flow via its
# server-side connector manager (not a browser), which can't do the redirect.
# Without them, Grok falls back to showing the manual OAuth credentials form,
# which lets the user fill in /authorize and /token — and the browser-based
# redirect flow works correctly.


@mcp.custom_route("/authorize", methods=["GET"])
async def authorize(request: Request):
    """OAuth authorization endpoint — auto-approves and redirects back with a code."""
    params = dict(request.query_params)
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")

    if not redirect_uri:
        return JSONResponse({"error": "missing redirect_uri"}, status_code=400)

    code = secrets.token_urlsafe(32)
    _auth_codes[code] = (redirect_uri, time.time() + 300)  # 5-min expiry

    sep = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{sep}code={code}"
    if state:
        location += f"&state={state}"

    return RedirectResponse(location, status_code=302)


@mcp.custom_route("/token", methods=["POST"])
async def token(request: Request):
    """OAuth token endpoint — exchanges auth code for the MCP Bearer token."""
    try:
        form = await request.form()
        data = dict(form)
    except Exception:
        data = {}

    if not data:
        try:
            data = await request.json()
        except Exception:
            data = {}

    grant_type = data.get("grant_type", "")
    code = data.get("code", "")

    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    entry = _auth_codes.pop(code, None)
    if entry is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    _, expires_at = entry
    if time.time() > expires_at:
        return JSONResponse({"error": "invalid_grant", "error_description": "code expired"}, status_code=400)

    return JSONResponse({
        "access_token": MCP_AUTH_TOKEN,
        "token_type": "bearer",
        "expires_in": 315360000,  # ~10 years — effectively permanent
    })


# ── Health check ──────────────────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({
        "status": "ok",
        "service": "watchtower-mcp",
        "version": "1.1.0-oauth",
        "tools": [
            "watchtower_run_screen",
            "watchtower_get_momentum",
            "watchtower_get_bearish_ideas",
            "watchtower_get_gmmss_context",
        ],
    })


# ── ASGI app with Bearer auth on /mcp ─────────────────────────────────────────

raw_app = mcp.streamable_http_app()


class AuthASGIWrapper:
    """Lightweight ASGI wrapper — enforces Bearer auth on /mcp, passes OAuth paths through.

    Also rewrites the Host header to 'localhost' for /mcp requests so the MCP SDK's
    built-in DNS-rebinding protection doesn't reject Railway's public hostname (421).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if MCP_AUTH_TOKEN and path.startswith("/mcp") and path not in PUBLIC_PATHS:
                headers = dict(scope.get("headers", []))
                host = headers.get(b"host", b"").decode("utf-8", errors="replace")
                base_url = f"https://{host}" if host else ""
                auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
                if not auth.lower().startswith("bearer "):
                    await self._unauthorized(send, base_url)
                    return
                token = auth.split(" ", 1)[1].strip()
                if token != MCP_AUTH_TOKEN:
                    await self._unauthorized(send, base_url)
                    return

        await self.app(scope, receive, send)

    async def _unauthorized(self, send, base_url: str = ""):
        body = b'{"error":"Unauthorized"}'
        resource_metadata = f"{base_url}/.well-known/oauth-protected-resource"
        www_auth = f'Bearer realm="watchtower", resource_metadata="{resource_metadata}"'
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", www_auth.encode()),
        ]})
        await send({"type": "http.response.body", "body": body})


app = AuthASGIWrapper(raw_app)

# Start background scheduler for intraday email alerts
try:
    from alerts.scheduler import start_scheduler
    _scheduler = start_scheduler()
except Exception as _sched_err:
    import logging
    logging.getLogger(__name__).warning(f"Scheduler not started: {_sched_err}")


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("server:app", host=host, port=PORT, log_level="info")
