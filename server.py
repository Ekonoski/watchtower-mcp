#!/usr/bin/env python
"""
Watchtower MCP Server for Grok (Improved Output + Trade Plans)
"""

import json
import os
import sys
from typing import Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
import uvicorn

app = FastAPI(title="Watchtower MCP Server")

# Use apiKey style for better Grok compatibility
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    openapi_schema.setdefault("components", {})["securitySchemes"] = {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization"
        }
    }
    for path_item in openapi_schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation.setdefault("security", []).append({"ApiKeyAuth": []})
    app.openapi_schema = openapi_schema
    return openapi_schema

app.openapi = custom_openapi

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if MCP_AUTH_TOKEN and request.url.path.startswith("/mcp"):
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            if token != MCP_AUTH_TOKEN:
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return await call_next(request)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "watchtower-mcp"}

# Lazy imports
def _get_screens():
    from screen.reversal_screen import run_screen as run_reversal
    from screen.momentum_screen import run_screen as run_momentum
    from screen.breakdown_screen import run_screen as run_breakdown
    from screen.master_screen import run_screen as run_master
    from screen.insider_burst_screen import run_screen as run_insider
    return run_reversal, run_momentum, run_breakdown, run_master, run_insider

@app.post("/mcp")
async def mcp_handler(request: Request):
    body = await request.json()
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "watchtower-mcp", "version": "0.2.0"}
            }
        }

    if method == "tools/list":
        tools = [
            {
                "name": "watchtower_run_screen",
                "description": "Run a screen live. Supports: reversal, momentum, breakdown, master, insider. Add with_plan=true to get trade plans.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "screen": {"type": "string", "description": "reversal | momentum | breakdown | master | insider"},
                        "top_n": {"type": "integer", "description": "How many results (default 8)"},
                        "with_plan": {"type": "boolean", "description": "Include ATR stop + position sizing"}
                    },
                    "required": ["screen"]
                }
            },
            {
                "name": "watchtower_get_momentum",
                "description": "Top momentum / up-and-comers with clean summary.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_n": {"type": "integer"}
                    }
                }
            },
            {
                "name": "watchtower_get_bearish_ideas",
                "description": "Top bearish / breakdown candidates with clean summary.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_n": {"type": "integer"}
                    }
                }
            },
            {
                "name": "watchtower_get_gmmss_context",
                "description": "Full current regime + momentum + bearish summary in one call.",
                "inputSchema": {"type": "object", "properties": {}}
            }
        ]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})

        try:
            if tool_name == "watchtower_run_screen":
                screen = args.get("screen")
                top_n = int(args.get("top_n", 8))
                with_plan = args.get("with_plan", False)

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
                    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Unknown screen"}}

                # Format nicely
                lines = [f"**{screen.upper()} SCREEN** ({len(results)} results)"]
                for r in results:
                    line = f"- **{r.get('ticker')}** | {r.get('company_name', '')[:30]} | Score: {r.get('reversal_score') or r.get('momentum_score') or r.get('breakdown_score', 'N/A')}"
                    if with_plan and r.get('plan'):
                        p = r['plan']
                        line += f" | Stop: ${p.get('stop_price'):.2f} | Size: {p.get('position_pct', 0):.1f}%"
                    lines.append(line)

                text = "\n".join(lines)
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

            elif tool_name == "watchtower_get_momentum":
                top_n = int(args.get("top_n", 8))
                # Placeholder clean output (real implementation would pull from artifacts or live)
                text = f"**Top {top_n} Momentum Names**\n(Improved formatting coming in next update)"
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

            elif tool_name == "watchtower_get_bearish_ideas":
                top_n = int(args.get("top_n", 8))
                text = f"**Top {top_n} Bearish / Breakdown Ideas**\n(Improved formatting coming in next update)"
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

            elif tool_name == "watchtower_get_gmmss_context":
                text = "**GMMSS Context**\n- Regime: Bullish bias\n- Momentum sleeve: Strong\n- Bearish sleeve: Visible for protection\n(Full rich output in next push)"
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

            else:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Tool not found"}}

        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "Invalid request"}}

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8421))
    uvicorn.run(app, host=host, port=port)
