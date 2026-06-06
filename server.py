#!/usr/bin/env python
"""
Minimal Watchtower MCP Server for Grok - Bearer Token Auth
"""

import json
import os
import sys
from typing import Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="Watchtower MCP Server", version="0.1.0")

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN")

# Force Bearer auth in OpenAPI for Grok
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = app.openapi()
    openapi_schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": "MCP_AUTH_TOKEN from Railway environment variables"
        }
    }
    # Apply to /mcp
    if "/mcp" in openapi_schema.get("paths", {}):
        openapi_schema["paths"]["/mcp"].setdefault("security", []).append({"BearerAuth": []})
    app.openapi_schema = openapi_schema
    return openapi_schema

app.openapi = custom_openapi

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if MCP_AUTH_TOKEN and request.url.path.startswith("/mcp"):
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token != MCP_AUTH_TOKEN:
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return await call_next(request)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "watchtower-mcp", "gmmss": True}

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    # Placeholder - expand with real tools later
    body = await request.json()
    return {
        "jsonrpc": "2.0",
        "id": body.get("id"),
        "result": {
            "content": [{"type": "text", "text": "✅ Watchtower MCP is connected! Ready for stock queries."}]
        }
    }

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8421))
    print(f"🚀 Starting Watchtower MCP on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
