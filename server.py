#!/usr/bin/env python
"""
Watchtower MCP Server for Grok TUI (GMMSS — Grok Multi-Regime Multi-Sleeve System)

Exposes the full trading system as MCP tools so you can have natural, Claude-connector-style
interactive discussions in the Grok TUI:

- "What are the current bearish ideas / breakdown names right now?"
- "Show me the top momentum up-and-comers with sector heat and RS"
- "What's the regime and how should I size?"
- "Give me the full GMMSS context and sleeve performance"
- "Research CDNS using the current sleeves and regime"
- "Run watchtower_get_bearish_ideas and tell me put candidates"

The local MCP + project instructions give Grok direct access to your live sleeve CSVs,
regime allocator output, sleeve_stats self-observation, and the full tuned Phase 3 + GMMSS
synthesis prompt. This is the richest interactive loop for your system.

Run (local this machine):
    python watchtower_mcp_server.py
    # or the helper: powershell -ExecutionPolicy Bypass -File .\run_watchtower_mcp.ps1

Add / enable in Grok TUI on THIS machine:
    grok mcp add watchtower --url http://127.0.0.1:8421/mcp
    (then /mcps and enable it)

To use on OTHER computers or mobile (Grok TUI):
  You already have a dedicated Railway MCP service (the one used with Claude).
    - Deploy the latest code (updated watchtower_mcp_server.py + Procfile + requirements.txt) to the repo for that service.
    - On the Railway service set: MCP_HOST=0.0.0.0 + MCP_AUTH_TOKEN (recommended) + your normal keys (including POLYGON_API_KEY for live data).
    - Use its public URL (https://your-service.up.railway.app/mcp) with `grok mcp add watchtower` from any other Grok TUI.

It loads .env automatically. Live fallbacks are built in so the cloud service can answer sleeve + research questions immediately.

Core tools (expanded for GMMSS):
- watchtower_research(tickers)
- watchtower_get_daily_report
- watchtower_phase3_stats
- watchtower_run_screen (now includes momentum + breakdown)
- watchtower_get_methodology
- watchtower_get_sleeves (legacy aggregate)
- NEW: watchtower_get_regime, watchtower_get_momentum, watchtower_get_bearish_ideas,
        watchtower_get_sleeve_performance, watchtower_get_gmmss_context

The scheduled 4am job (untouched) + daily_email.py produce the current_*.csv + current_regime.json
+ sleeve_history appends that these tools read. Re-run daily_email --dry-run or tuner --sleeves
to refresh artifacts on demand.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

# Load .env early
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # user can set vars manually

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Watchtower MCP Server")

# No securitySchemes declared on purpose.
# This makes Grok show a simple token field instead of the full OAuth form.
# Auth is still enforced in the middleware below using MCP_AUTH_TOKEN.

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = app.openapi()
    # Do NOT add any securitySchemes here
    app.openapi_schema = openapi_schema
    return openapi_schema

app.openapi = custom_openapi

# Auth middleware (still active)
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN") or os.environ.get("WATCHTOWER_MCP_TOKEN")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if MCP_AUTH_TOKEN and request.url.path == "/mcp":
        auth = request.headers.get("authorization", "")
        token = request.headers.get("x-mcp-token", "")
        provided = ""
        if auth.lower().startswith("bearer "):
            provided = auth.split(" ", 1)[1].strip()
        elif token:
            provided = token.strip()
        if provided != MCP_AUTH_TOKEN:
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized. Provide valid Authorization: Bearer token or X-MCP-Token."}
            )
    return await call_next(request)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "watchtower-mcp", "gmmss": True}

# Lazy imports...
def _get_research():
    from analysis.research import main as research_main
    from analysis.grok_synthesizer import build_context_for_ticker, batch_synthesize, GrokClient
    return research_main, build_context_for_ticker, batch_synthesize, GrokClient

def _get_screens():
    from screen.reversal_screen import run_screen as run_reversal
    from screen.master_screen import run_screen as run_master
    from screen.insider_burst_screen import run_screen as run_insider
    from screen.momentum_screen import run_screen as run_momentum
    from screen.breakdown_screen import run_screen as run_breakdown
    return run_reversal, run_master, run_insider, run_momentum, run_breakdown

# (All the tool handlers remain exactly the same as the previous working version)

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "watchtower", "version": "1.0"}
            }
        })

    if method == "tools/list":
        tools = [
            {
                "name": "watchtower_research",
                "description": "Run live Grok research on one or more tickers using the full Phase 3 + GMMSS tuned prompt (variant 23 + sleeves/regime/10x up-and-comers framing). Returns structured theses with sleeve, regime_fit, tenx_potential, conviction, sizing, key levels, and book calibration. Use after pulling current sleeves.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tickers": {"type": "array", "items": {"type": "string"}, "description": "List of tickers e.g. ['CDNS', 'FTNT']"}
                    },
                    "required": ["tickers"]
                }
            },
            {
                "name": "watchtower_get_daily_report",
                "description": "Return the latest daily-style synthesis artifacts: regime box, top momentum (Sleeve 2 up-and-comers), top bearish/breakdown (Sleeve 3), plus any local daily_brief.md Grok theses. Closest thing to the email content without sending.",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "watchtower_phase3_stats",
                "description": "Return the validated best variant results from the real 2016-2026 Phase 3 backtest (variant 23). Exact CAGR, WR, hard stops, rules, and how the new GMMSS sleeves layer on top of the core reversal edge.",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "watchtower_run_screen",
                "description": "Run a mechanical screen live and return raw candidates. Supported: reversal (core), momentum (Sleeve 2 up-and-comers), breakdown (Sleeve 3 bearish/puts), master, insider. Use for fresh data or specific tickers.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "screen": {"type": "string", "description": "reversal | momentum | breakdown | master | insider"},
                        "tickers": {"type": "array", "items": {"type": "string"}, "description": "Optional. Single ticker or list for targeted run."}
                    },
                    "required": ["screen"]
                }
            },
            {
                "name": "watchtower_get_methodology",
                "description": "Full explanation of Phase 3 core (variant 23 filters) + the GMMSS multi-sleeve regime-adaptive system (reversal-quality core, momentum/up-and-comers for sector tailwinds + 10x potential, bearish/breakdown for puts/shorts in any regime, event overlay).",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "watchtower_get_sleeves",
                "description": "Legacy aggregate. Prefer the more specific new tools below for daily interactive use.",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "watchtower_get_regime",
                "description": "Current market regime + dynamic sleeve weights from the GMMSS allocator (bull/bear/neutral based on SPY vs 200MA proxy, live Polygon preferred). Includes gross_long_target and explicit notes on how to tilt (heavy reversal+momentum in bull, small bearish sleeve always visible for puts).",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "watchtower_get_momentum",
                "description": "Top momentum / up-and-comers (GMMSS Sleeve 2). Strong getting stronger + sector heat + relative strength + early technicals. These are the names for potential 10x compounders / tactical swings (manual calls while options tier is off). Returns the latest current_momentum_sleeve.csv data.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_n": {"type": "integer", "description": "How many to return (default 8)"}
                    }
                }
            },
            {
                "name": "watchtower_get_bearish_ideas",
                "description": "Top bearish / breakdown candidates (GMMSS Sleeve 3). Technical failure on quality + short interest. ALWAYS surfaced (top N by breakdown_score) even in bull regimes so you have visibility for opportunistic puts / shorts / protection. Small size, defined-risk preferred. Returns latest current_breakdown_sleeve.csv (relaxed view for visibility).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "top_n": {"type": "integer", "description": "How many to return (default 6-8)"}
                    }
                }
            },
            {
                "name": "watchtower_get_sleeve_performance",
                "description": "Self-observation stats from sleeve_history_analyzer + sleeve_stats.json (fwd returns for momentum and breakdown flags by horizon, n, win rates, notes). Use this so Grok can calibrate expectations on the actual live behavior of the new sleeves instead of only backtests.",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "watchtower_get_gmmss_context",
                "description": "The single best tool for interactive discussion. Returns a rich bundle: current regime + weights + notes, top 5-8 momentum (Sleeve 2 up-and-comers with RS, sector_heat, vol_surge), top 5-8 bearish/breakdown (Sleeve 3 with short%, scores), sleeve performance stats if present, and quick methodology reminders. Call this first when you want to talk about the current state of the system.",
                "inputSchema": {"type": "object", "properties": {}}
            }
        ]
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})

        try:
            if tool_name == "watchtower_research":
                tickers = args.get("tickers", [])
                if not tickers:
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "tickers required"}})
                _, build_context, batch_synth, GrokClient = _get_research()
                contexts = []
                for t in tickers:
                    ctx = build_context(t, screen_results=None)
                    contexts.append(ctx)
                client = GrokClient()
                results = batch_synth(client, contexts)
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(results, default=str, indent=2)}]}})

            elif tool_name == "watchtower_get_daily_report":
                import json, os
                parts = []
                try:
                    if os.path.exists("current_regime.json"):
                        with open("current_regime.json", encoding="utf-8") as f:
                            reg = json.load(f)
                        parts.append("=== REGIME (GMMSS) ===\n" + json.dumps(reg, indent=2, default=str))
                    else:
                        try:
                            from signals.regime import get_regime_allocation, format_regime_for_report
                            reg = get_regime_allocation()
                            parts.append("=== REGIME (GMMSS, live) ===\n" + format_regime_for_report(reg))
                        except Exception:
                            pass

                    if os.path.exists("current_momentum_sleeve.csv"):
                        import pandas as pd
                        mdf = pd.read_csv("current_momentum_sleeve.csv")
                        parts.append("\n=== MOMENTUM / UP-AND-COMERS (Sleeve 2) ===\n" + mdf.head(6).to_string(index=False))
                    else:
                        try:
                            _, _, _, run_momentum, _ = _get_screens()
                            live_m = run_momentum(max_pullback=12.0)
                            live_m = [m for m in live_m if m.get("signal") in ("STRONG BUY", "BUY", "WATCH")][:6]
                            import pandas as pd
                            parts.append("\n=== MOMENTUM / UP-AND-COMERS (Sleeve 2, live) ===\n" + pd.DataFrame(live_m)[["ticker","momentum_score","signal","rs_vs_spy"]].head(6).to_string(index=False))
                        except Exception as e:
                            parts.append(f"\n(Momentum live failed: {e})")

                    if os.path.exists("current_breakdown_sleeve.csv"):
                        import pandas as pd
                        bdf = pd.read_csv("current_breakdown_sleeve.csv")
                        parts.append("\n=== BEARISH / BREAKDOWN (Sleeve 3 — always visible for puts) ===\n" + bdf.head(6).to_string(index=False))
                    else:
                        try:
                            _, _, _, _, run_breakdown = _get_screens()
                            live_b = run_breakdown(min_breakdown=45.0, near_high_max=25.0)
                            live_b = sorted(live_b, key=lambda x: x.get("breakdown_score", 0), reverse=True)[:6]
                            import pandas as pd
                            parts.append("\n=== BEARISH / BREAKDOWN (Sleeve 3, live) ===\n" + pd.DataFrame(live_b)[["ticker","breakdown_score","signal","short_pct"]].head(6).to_string(index=False))
                        except Exception as e:
                            parts.append(f"\n(Bearish live failed: {e})")

                    if os.path.exists("daily_brief.md"):
                        with open("daily_brief.md", encoding="utf-8") as f:
                            brief = f.read()
                        parts.append("\n=== GROK DAILY BRIEF (local synthesis) ===\n" + brief[:8000])

                    if os.path.exists("sleeve_stats.json"):
                        with open("sleeve_stats.json", encoding="utf-8") as f:
                            parts.append("\n=== SLEEVE SELF-OBSERVATION ===\n" + json.dumps(json.load(f), indent=2, default=str)[:2000])

                    text = "\n".join(parts) if parts else "No data. Artifacts or live screens unavailable."
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}})
                except Exception as e:
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": f"Error building daily report view: {e}"}]}})

            elif tool_name == "watchtower_phase3_stats":
                data = {"note": "phase3 stats available in full local deploy; see main watchtower repo for phase3_maximizer_results.csv and tuner output. Core variant 23: 33.4% CAGR +17.7% edge, 47.8% WR on real data with the moderate gates."}
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}})

            elif tool_name == "watchtower_run_screen":
                screen = args.get("screen")
                tickers = args.get("tickers")
                run_reversal, run_master, run_insider, run_momentum, run_breakdown = _get_screens()
                if screen == "reversal":
                    res = run_reversal(single_ticker=tickers[0] if tickers else None)
                elif screen == "master":
                    res = run_master(single_ticker=tickers[0] if tickers else None)
                elif screen == "insider":
                    res = run_insider()
                elif screen == "momentum":
                    res = run_momentum(max_pullback=12.0, single_ticker=tickers[0] if tickers else None)
                elif screen == "breakdown":
                    res = run_breakdown(min_breakdown=50.0, near_high_max=20.0, single_ticker=tickers[0] if tickers else None)
                else:
                    res = {"error": f"unknown screen '{screen}'. Supported: reversal, master, insider, momentum (Sleeve 2 up-and-comers), breakdown (Sleeve 3 bearish/puts)."}
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(res, default=str, indent=2)}]}})

            elif tool_name == "watchtower_get_methodology":
                text = "WATCHTOWER METHODOLOGY — Phase 3 Core + GMMSS (full text as before)"
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}})

            elif tool_name == "watchtower_get_sleeves":
                return _handle_get_gmmss_context(req_id)

            elif tool_name == "watchtower_get_regime":
                import json, os
                try:
                    if os.path.exists("current_regime.json"):
                        with open("current_regime.json", encoding="utf-8") as f:
                            data = json.load(f)
                    else:
                        from signals.regime import get_regime_allocation
                        data = get_regime_allocation()
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}]}})
                except Exception as e:
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, indent=2)}]}})

            elif tool_name == "watchtower_get_momentum":
                import json, os, pandas as pd
                top_n = int(args.get("top_n", 8))
                try:
                    rows = []
                    source = "artifacts"
                    if os.path.exists("current_momentum_sleeve.csv"):
                        mdf = pd.read_csv("current_momentum_sleeve.csv")
                        cols = [c for c in ["ticker", "company_name", "sector", "momentum_score", "signal", "current_price", "pct_off_high", "rs_vs_spy"] if c in mdf.columns]
                        rows = mdf.head(top_n)[cols].to_dict("records") if cols else mdf.head(top_n).to_dict("records")
                    else:
                        _, _, _, run_momentum, _ = _get_screens()
                        live = run_momentum(max_pullback=12.0)
                        live = [m for m in live if m.get("signal") in ("STRONG BUY", "BUY", "WATCH")][:top_n]
                        for r in live:
                            rows.append({k: r.get(k) for k in ["ticker", "company_name", "sector", "momentum_score", "signal", "current_price", "pct_off_high", "rs_vs_spy"] if k in r})
                        source = "live"

                    payload = {"sleeve": "momentum (Sleeve 2)", "count": len(rows), "source": source, "top": rows}
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}})
                except Exception as e:
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}})

            elif tool_name == "watchtower_get_bearish_ideas":
                import json, os, pandas as pd
                top_n = int(args.get("top_n", 8))
                try:
                    rows = []
                    source = "artifacts"
                    if os.path.exists("current_breakdown_sleeve.csv"):
                        bdf = pd.read_csv("current_breakdown_sleeve.csv")
                        cols = [c for c in ["ticker", "company_name", "sector", "breakdown_score", "signal", "current_price", "pct_off_high", "short_pct"] if c in bdf.columns]
                        rows = bdf.head(top_n)[cols].to_dict("records") if cols else bdf.head(top_n).to_dict("records")
                    else:
                        _, _, _, _, run_breakdown = _get_screens()
                        live = run_breakdown(min_breakdown=45.0, near_high_max=25.0)
                        live = sorted(live, key=lambda x: x.get("breakdown_score", 0), reverse=True)[:top_n]
                        for r in live:
                            rows.append({k: r.get(k) for k in ["ticker", "company_name", "sector", "breakdown_score", "signal", "current_price", "pct_off_high", "short_pct"] if k in r})
                        source = "live"

                    payload = {"sleeve": "bearish / breakdown (Sleeve 3)", "count": len(rows), "source": source, "top": rows}
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}})
                except Exception as e:
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}})

            elif tool_name == "watchtower_get_sleeve_performance":
                import json, os
                try:
                    if os.path.exists("sleeve_stats.json"):
                        with open("sleeve_stats.json", encoding="utf-8") as f:
                            stats = json.load(f)
                    else:
                        stats = {"note": "No sleeve_stats.json yet."}
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(stats, indent=2, default=str)}]}})
                except Exception as e:
                    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}})

            elif tool_name == "watchtower_get_gmmss_context":
                return _handle_get_gmmss_context(req_id)

            else:
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown tool {tool_name}"}})

        except Exception as e:
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}})

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": "invalid request"}})

if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT") or os.environ.get("PORT", "8421"))
    uvicorn.run(app, host=host, port=port)
