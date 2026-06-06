#!/usr/bin/env python
"""
Watchtower MCP Server for Grok - Improved for Natural Language Use
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
                "serverInfo": {"name": "watchtower-mcp", "version": "0.3.0"}
            }
        }

    if method == "tools/list":
        tools = [
            {
                "name": "watchtower_run_screen",
                "description": "Run one of Watchtower's stock screens live. Use this when the user wants to find stocks matching a specific strategy like reversal setups, momentum stocks, or breakdown candidates. Supports reversal (beaten-down quality turning up), momentum (strong up-and-comers), breakdown (bearish ideas), master, and insider screens.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "screen": {
                            "type": "string",
                            "description": "Which screen to run. Must be one of: reversal, momentum, breakdown, master, insider"
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "How many top results to return. Default is 8."
                        },
                        "with_plan": {
                            "type": "boolean",
                            "description": "If true, include suggested trade plan with ATR-based stop loss and position size for each result."
                        }
                    },
                    "required": ["screen"]
                }
            },
            {
                "name": "watchtower_get_momentum",
                "description": "Get the current top momentum / up-and-comers stocks according to Watchtower's momentum sleeve. Use this when the user asks for strong stocks, accelerating names, or up-and-coming ideas.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_n": {
                            "type": "integer",
                            "description": "Number of stocks to return. Default is 8."
                        }
                    }
                }
            },
            {
                "name": "watchtower_get_bearish_ideas",
                "description": "Get the current top bearish / breakdown candidates from Watchtower's bearish sleeve. Use this when the user wants short ideas, protection candidates, or stocks that are breaking down.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_n": {
                            "type": "integer",
                            "description": "Number of stocks to return. Default is 8."
                        }
                    }
                }
            },
            {
                "name": "watchtower_get_gmmss_context",
                "description": "Get a complete current snapshot of the Watchtower system including market regime, top momentum names, top bearish ideas, and overall methodology context. This is the best single tool to call when the user wants an overview of the current state of the system.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "watchtower_run_screen":
            # Placeholder response for now
            screen = args.get("screen", "reversal")
            top_n = args.get("top_n", 5)
            with_plan = args.get("with_plan", False)
            text = f"Running {screen} screen (top {top_n}) with trade plan = {with_plan}. Real results will come from the full implementation."
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

        elif tool_name == "watchtower_get_momentum":
            text = "Top momentum stocks would be returned here from the real implementation."
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

        elif tool_name == "watchtower_get_bearish_ideas":
            text = "Top bearish ideas would be returned here from the real implementation."
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

        elif tool_name == "watchtower_get_gmmss_context":
            text = "Full GMMSS context (regime + momentum + bearish + methodology) would be returned here."
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}

        else:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Tool not found"}}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "Invalid request"}}

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8421))
    uvicorn.run(app, host=host, port=port)
