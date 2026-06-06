#!/usr/bin/env python
"""
Watchtower MCP Server - Official MCP SDK version

This version uses the standard mcp Python SDK (FastMCP) for maximum compatibility
with Grok, Claude, and other MCP clients.

Replaces the previous custom raw JSON-RPC handler that was causing connection
and tool discovery issues.
"""

import os
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", 8080))

mcp = FastMCP(
    "watchtower",
    streamable_http_path="/mcp",
)

PUBLIC_PATHS = {"/health"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Require Bearer token for /mcp paths (except health)."""
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)
        if MCP_AUTH_TOKEN and path.startswith("/mcp"):
            auth = request.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            token = auth.split(" ", 1)[1].strip()
            if token != MCP_AUTH_TOKEN:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)


def _get_screens():
    """Lazy load the screen runners from the screen/ package."""
    from screen.reversal_screen import run_screen as run_reversal
    from screen.momentum_screen import run_screen as run_momentum
    from screen.breakdown_screen import run_screen as run_breakdown
    from screen.master_screen import run_screen as run_master
    from screen.insider_burst_screen import run_screen as run_insider
    return run_reversal, run_momentum, run_breakdown, run_master, run_insider


@mcp.tool()
def watchtower_run_screen(
    screen: str,
    top_n: int = 5,
    with_plan: bool = True,
) -> str:
    """Run one of Watchtower's stock screens live.

    Supports:
    - reversal: beaten-down quality stocks turning up (8/13 EMA, RSI recovery, etc.)
    - momentum: strong up-and-comers
    - breakdown: bearish ideas
    - master: broad fundamental composite
    - insider: insider activity driven

    Use with_plan=true to include suggested trade plan (ATR stop + position size).
    """
    run_reversal, run_momentum, run_breakdown, run_master, run_insider = _get_screens()

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
    else:
        return f"Unknown screen '{screen}'. Valid options: reversal, momentum, breakdown, master, insider"

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
def watchtower_get_gmmss_context() -> str:
    """Get full current context: regime + top momentum + top bearish ideas + methodology."""
    # For now return a summary; can be expanded with regime.json + sleeves
    return "GMMSS context: Bull regime (see current_regime.json in repo). Use watchtower_run_screen or the individual getters for live sleeves. Full synthesis available when all keys (POLYGON, XAI) are configured on Railway."


# Public health check (bypasses auth)
@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse(
        {
            "status": "ok",
            "service": "watchtower-mcp",
            "version": "1.0.0-sdk",
            "tools": ["watchtower_run_screen", "watchtower_get_momentum", "watchtower_get_bearish_ideas", "watchtower_get_gmmss_context"],
        }
    )


# Wrap the MCP app with auth middleware
raw_app = mcp.streamable_http_app()


class AuthASGIWrapper:
    """Lightweight ASGI wrapper for Bearer auth on MCP paths."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path not in PUBLIC_PATHS and MCP_AUTH_TOKEN and path.startswith("/mcp"):
                headers = dict(scope.get("headers", []))
                auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
                if not auth.lower().startswith("bearer "):
                    await self._send_error(send, 401, "Unauthorized")
                    return
                token = auth.split(" ", 1)[1].strip()
                if token != MCP_AUTH_TOKEN:
                    await self._send_error(send, 401, "Unauthorized")
                    return
        await self.app(scope, receive, send)

    async def _send_error(self, send, status: int, message: str):
        body = f'{{"error": "{message}"}}'.encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


app = AuthASGIWrapper(raw_app)


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("server:app", host=host, port=PORT, log_level="info")
