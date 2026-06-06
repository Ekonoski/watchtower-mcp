#!/usr/bin/env python
"""
Watchtower MCP Server for Grok
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

# Use apiKey style instead of http bearer.
# This often makes Grok show a simple token field.
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
    req_id = body.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "watchtower-mcp", "version": "0.1.0"}
            }
        }

    if method == "tools/list":
        tools = [
            {
                "name": "get_health",
                "description": "Health check",
                "inputSchema": {"type": "object", "properties": {}}
            }
        ]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

    if method == "tools/call":
        tool_name = body.get("params", {}).get("name")
        if tool_name == "get_health":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": "✅ Watchtower MCP is healthy"}]
                }
            }
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Tool not found"}}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "Invalid request"}}

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8421))
    uvicorn.run(app, host=host, port=port)
