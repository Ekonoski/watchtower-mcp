# Watchtower MCP Server for Grok

import os
import sys
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = FastAPI(title="Watchtower MCP Server")

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if MCP_AUTH_TOKEN and request.url.path.startswith("/mcp"):
        auth = request.headers.get("authorization", "")
        token = request.headers.get("x-mcp-token", "")
        provided = ""
        if auth.lower().startswith("bearer "):
            provided = auth.split(" ", 1)[1].strip()
        elif token:
            provided = token.strip()
        if provided != MCP_AUTH_TOKEN:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return await call_next(request)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "watchtower-mcp", "gmmss": True}

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    # Placeholder for now - full tools to be added later
    body = await request.json()
    return {"jsonrpc": "2.0", "id": body.get("id"), "result": {"content": [{"type": "text", "text": "Watchtower MCP connected successfully! Use your tools."}]}}

if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8421))
    uvicorn.run(app, host=host, port=port)
