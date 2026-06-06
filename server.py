#!/usr/bin/env python
"""
Watchtower MCP Server for Grok - Restored Functionality
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

# Lazy imports for screens
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
                "serverInfo": {"name": "watchtower-mcp", "version": "0.4.0"}
            }
        }

    if method == "tools/list":
        tools = [
            {
                "name": "watchtower_run_screen",
                "description": "Run one of Watchtower's stock screens live. Supports reversal (beaten-down quality turning up), momentum (strong up-and-comers), breakdown (bearish ideas), master, and insider. Use with_plan=true to get trade plans with ATR stops and position sizing.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "screen": {
                            "type": "string",
                            "description": "reversal | momentum | breakdown | master | insider"
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "How many results to return (default 8)"
                        },
                        "with_plan": {
                            "type": "boolean",
                            "description": "Include suggested trade plan (ATR stop + position size)"
                        }
                    },
                    "required": ["screen"]
                }
            },
            {
                "name": "watchtower_get_momentum",
                "description": "Get current top momentum / up-and-comers from Watchtower's momentum sleeve.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_n": {"type": "integer"}
                    }
                }
            },
            {
                "name": "watchtower_get_bearish_ideas",
                "description": "Get current top bearish / breakdown candidates from Watchtower's bearish sleeve.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_n": {"type": "integer"}
                    }
                }
            },
            {
                "name": "watchtower_get_gmmss_context",
                "description": "Get full current context: regime + top momentum + top bearish ideas + methodology.",
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
                    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Unknown screen type"}}

                # Format response nicely
                lines = [f"**{screen.upper()} SCREEN RESULTS** (Top {len(results)})"]
                for r in results:
                    score = r.get('reversal_score') or r.get('momentum_score') or r.get('breakdown_score', 'N/A')
                    line = f"- **{r.get('ticker')}** | {r.get('company_name', '')[:28]} | Score: {score}"
                    if with_plan and r.get('plan'):
                        p = r['plan']
                        line += f" | Stop: ${p.get('stop_price', 0):.2f} | Size: {p.get('position_pct', 0):.1f}%"
                    lines.append(line)

                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": "\n".join(lines)}]}}

            elif tool_name == "watchtower_get_momentum":
                top_n = int(args.get("top_n", 8))
                # Simple version - in full impl this would pull from artifacts or live
                text = f"Top {top_n} momentum stocks (real implementation would return actual data here)"
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

            elif tool_name == "watchtower_get_bearish_ideas":
                top_n = int(args.get("top_n", 8))
                text = f"Top {top_n} bearish/breakdown ideas (real implementation would return actual data here)"
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

            elif tool_name == "watchtower_get_gmmss_context":
                text = "Full GMMSS context would be returned here with regime + momentum + bearish + methodology."
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
