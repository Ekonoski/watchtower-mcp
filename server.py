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

mcp = FastMCP(
    "watchtower",
    streamable_http_path="/mcp",
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
    from screen.upcomer_screen import run_screen as run_upcomer
    return run_reversal, run_momentum, run_breakdown, run_master, run_insider, run_upcomer


@mcp.tool()
def watchtower_run_screen(
    screen: str,
    top_n: int = 5,
    with_plan: bool = True,
) -> str:
    """Run one of Watchtower's stock screens live.

    Supports:
    - reversal: beaten-down quality stocks turning up (8/13 EMA, RSI recovery, etc.)
    - momentum: strong getting stronger — near 52w highs, established trends
    - breakdown: bearish ideas, shorting candidates
    - master: broad fundamental composite
    - insider: insider activity driven
    - upcomer: hidden gems / 10x potential — off-radar small/mid caps breaking out of bases

    Use with_plan=true to include suggested trade plan (ATR stop + position size).
    """
    run_reversal, run_momentum, run_breakdown, run_master, run_insider, run_upcomer = _get_screens()

    if screen == "reversal":
        results = run_reversal(min_drawdown=15.0)[:top_n]
    elif screen == "momentum":
        results = run_momentum(max_pullback=12.0)[:top_n]
    elif screen == "breakdown":
        results = run_breakdown(min_breakdown=45.0)[:top_n]
    elif screen == "master":
        results = run_master()[:top_n]
    elif screen == "insider":
        results = run_insider()[:top_n]
    elif screen in ("upcomer", "hidden_gems", "gems"):
        results = run_upcomer(min_score=30.0, top_n=top_n, with_synthesis=False)
    else:
        return f"Unknown screen '{screen}'. Valid options: reversal, momentum, breakdown, master, insider, upcomer"

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

    return "\n".join(lines)


@mcp.tool()
def watchtower_get_momentum(top_n: int = 5) -> str:
    """Get current top momentum / up-and-comers from Watchtower's momentum sleeve."""
    _, run_momentum, *_ = _get_screens()
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
def watchtower_get_hidden_gems(top_n: int = 10) -> str:
    """
    Scan for hidden gems and up-and-comer stocks — completely separate from the momentum screen.

    Scans the full daily_prices universe (not just the quality 40) to find truly
    off-radar small/mid cap stocks that:
    - Are 18-60% off their 52-week highs (NOT near highs like momentum names)
    - Are breaking out of long consolidation bases on expanding volume
    - Show fundamental acceleration (QoQ revenue/earnings improving)
    - Have big upside to analyst price targets (or no analyst coverage yet)
    - Small/mid cap bias — the less covered, the more potential

    These are early-stage 10x candidates, not established momentum names.
    """
    *_, run_upcomer = _get_screens()
    results = run_upcomer(min_score=30.0, top_n=top_n, with_synthesis=False)
    if not results:
        return "No hidden gems found above threshold right now."

    lines = [f"**HIDDEN GEMS / UP-AND-COMER SCREEN** (Top {len(results)})"]
    lines.append("*Stocks 18-60% off highs, breaking bases, with fundamental acceleration*\n")
    for r in results:
        score = r.get("score", 0)
        ticker = r.get("ticker", "")
        price = r.get("current_price", 0)
        dd = r.get("drawdown_pct", 0)
        rsi = r.get("rsi") or 0
        rationale = r.get("rationale", "")
        pt = r.get("price_target_avg")
        upside_str = ""
        if pt and price > 0:
            upside = (pt - price) / price * 100
            upside_str = f" | PT upside: {upside:+.0f}%"
        lines.append(
            f"- **{ticker}** | Score: {score:.0f} | ${price:.2f} | "
            f"Off high: {dd:.0f}% | RSI: {rsi:.0f}{upside_str} | {rationale}"
        )
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_gmmss_context() -> str:
    """Get full current context: regime + top momentum + top bearish ideas + methodology."""
    return "GMMSS context: Bull regime (see current_regime.json in repo). Use watchtower_run_screen or the individual getters for live sleeves. Full synthesis available when all keys (POLYGON, XAI) are configured on Railway."


# ── OAuth 2.0 / PKCE endpoints ────────────────────────────────────────────────

@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_server_metadata(request: Request):
    base = str(request.base_url).rstrip("/")
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request: Request):
    base = str(request.base_url).rstrip("/")
    return JSONResponse({
        "resource": base,
        "authorization_servers": [base],
    })


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
    """Lightweight ASGI wrapper — enforces Bearer auth on /mcp, passes OAuth paths through."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if MCP_AUTH_TOKEN and path.startswith("/mcp") and path not in PUBLIC_PATHS:
                headers = dict(scope.get("headers", []))
                auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
                if not auth.lower().startswith("bearer "):
                    await self._unauthorized(send)
                    return
                token = auth.split(" ", 1)[1].strip()
                if token != MCP_AUTH_TOKEN:
                    await self._unauthorized(send)
                    return
        await self.app(scope, receive, send)

    async def _unauthorized(self, send):
        body = b'{"error":"Unauthorized"}'
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b"Bearer"),
        ]})
        await send({"type": "http.response.body", "body": body})


app = AuthASGIWrapper(raw_app)


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("server:app", host=host, port=PORT, log_level="info")
