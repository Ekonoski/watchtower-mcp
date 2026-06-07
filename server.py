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
def watchtower_analyze_ticker(ticker: str, with_synthesis: bool = True) -> str:
    """
    Run a single stock through ALL Watchtower screens and return a complete picture.

    Covers every sleeve:
    - Reversal (Sleeve 1): beaten-down quality setup?
    - Momentum (Sleeve 2): strong getting stronger?
    - Breakdown (Sleeve 3): bearish/short candidate?
    - Master: fundamental composite score
    - Insider: net insider buying?
    - Volume Burst: abnormal volume signal?
    - Upcomer / Hidden Gem: off-radar 10x potential?
    - Intraday: live signal right now (GAP_AND_GO, VWAP_BREAKOUT, etc.)?

    Returns a ranked multi-sleeve analysis with scores and rationale for each.
    Use this when you want a full 360° view of any stock.
    """
    ticker = ticker.upper().strip()
    run_reversal, run_momentum, run_breakdown, run_master, run_insider, run_upcomer = _get_screens()

    sleeve_results = []

    # Run every screen with the single ticker
    screen_configs = [
        ("REVERSAL",     lambda: run_reversal(min_drawdown=5.0, single_ticker=ticker)),
        ("MOMENTUM",     lambda: run_momentum(max_pullback=20.0, single_ticker=ticker)),
        ("BREAKDOWN",    lambda: run_breakdown(min_breakdown=20.0, single_ticker=ticker)),
        ("MASTER",       lambda: run_master(single_ticker=ticker)),
        ("INSIDER",      lambda: run_insider(single_ticker=ticker)),
        ("UPCOMER",      lambda: run_upcomer(min_score=0.0, top_n=1, single_ticker=ticker)),
    ]

    for sleeve_name, fn in screen_configs:
        try:
            results = fn()
            if results and not results[0].get("error"):
                r = results[0]
                score = (
                    r.get("reversal_score") or r.get("momentum_score") or
                    r.get("breakdown_score") or r.get("score") or 0
                )
                sleeve_results.append({
                    "sleeve": sleeve_name,
                    "score": score,
                    "data": r,
                })
        except Exception as e:
            sleeve_results.append({"sleeve": sleeve_name, "score": 0, "error": str(e)[:80]})

    # Intraday — live snapshot
    try:
        from screen.intraday_screen import run_screen as run_intraday
        intraday = run_intraday(min_score=0.0, single_ticker=ticker)
        if intraday and not intraday[0].get("error"):
            r = intraday[0]
            sleeve_results.append({
                "sleeve": "INTRADAY",
                "score": r.get("score", 0),
                "data": r,
            })
    except Exception as e:
        sleeve_results.append({"sleeve": "INTRADAY", "score": 0, "error": str(e)[:80]})

    # Volume burst
    try:
        from screen.volume_burst_screen import run_screen as run_volume_burst
        vb = run_volume_burst(single_ticker=ticker, min_surge=1.0)
        if vb and not vb[0].get("error"):
            r = vb[0]
            sleeve_results.append({
                "sleeve": "VOLUME_BURST",
                "score": r.get("score", 0),
                "data": r,
            })
    except Exception as e:
        sleeve_results.append({"sleeve": "VOLUME_BURST", "score": 0, "error": str(e)[:80]})

    # Format output
    lines = [f"## Watchtower Full Analysis — ${ticker}", ""]

    # Sort by score descending so strongest signals appear first
    scored = [s for s in sleeve_results if not s.get("error")]
    errored = [s for s in sleeve_results if s.get("error")]
    scored.sort(key=lambda x: x["score"] or 0, reverse=True)

    for s in scored:
        sleeve = s["sleeve"]
        score = s["score"] or 0
        data = s.get("data", {})

        # Signal strength indicator
        if score >= 70:
            strength = "🔥 STRONG"
        elif score >= 50:
            strength = "✅ MODERATE"
        elif score >= 30:
            strength = "👀 WEAK"
        else:
            strength = "⬜ LOW"

        line = f"**{sleeve}** | {strength} | Score: {score:.0f}"

        # Sleeve-specific detail
        if sleeve == "INTRADAY":
            signal = data.get("signal_type", "")
            rationale = data.get("rationale", "")
            change = data.get("change_pct", 0)
            vol_pace = data.get("vol_pace_ratio", 0)
            if signal:
                line += f" | Signal: {signal} | Chg: {change:+.1f}% | Vol pace: {vol_pace:.1f}x"
            if rationale:
                line += f"\n  → {rationale}"
        elif sleeve == "UPCOMER":
            dd = data.get("drawdown_pct", 0)
            rationale = data.get("rationale", "")
            line += f" | Off high: {dd:.0f}%"
            if rationale:
                line += f"\n  → {rationale}"
        elif sleeve == "VOLUME_BURST":
            signal = data.get("signal", "")
            surge = data.get("surge_ratio", 0)
            line += f" | Signal: {signal} | Surge: {surge:.1f}x"
        else:
            rationale = data.get("rationale", "") or data.get("plan_rationale", "")
            if rationale:
                line += f"\n  → {rationale}"

        lines.append(line)

    if errored:
        lines.append("")
        lines.append("*Sleeves with errors: " + ", ".join(s["sleeve"] for s in errored) + "*")

    # Social buzz — live X sentiment via Grok
    try:
        from analysis.social_buzz import query_ticker_sentiment, format_buzz_for_display
        buzz = query_ticker_sentiment(ticker)
        lines.append("")
        lines.append("**X / SOCIAL BUZZ**")
        lines.append(format_buzz_for_display(ticker, buzz))
    except Exception:
        pass

    # Grok synthesis across all sleeves
    if with_synthesis:
        try:
            from analysis.grok_client import GrokClient
            grok = GrokClient()

            context = f"Full Watchtower analysis for ${ticker}:\n"
            for s in scored:
                context += f"- {s['sleeve']}: score {s['score']:.0f}"
                data = s.get("data", {})
                rationale = data.get("rationale", "") or data.get("signal_type", "")
                if rationale:
                    context += f" — {rationale}"
                context += "\n"

            # Include social sentiment in synthesis context if available
            try:
                from analysis.social_buzz import query_ticker_sentiment
                buzz = query_ticker_sentiment(ticker)
                context += (
                    f"\nX/Social: {buzz.get('sentiment','neutral')} "
                    f"(score {buzz.get('sentiment_score', 0):+.2f}, "
                    f"{buzz.get('buzz_level','low')} buzz) — {buzz.get('summary','')}"
                )
            except Exception:
                pass

            resp = grok.chat(
                system=(
                    "You are Eric Konoski's personal trading analyst on the Watchtower GMMSS system. "
                    "Given multi-sleeve scores AND live X/social sentiment for a single stock, "
                    "synthesize a clear, actionable read. Be direct. State the dominant signal, "
                    "conviction level, best entry approach, and the main risk. "
                    "Factor in social sentiment as a confirming or contradicting signal. 3-4 sentences max."
                ),
                user=context,
                json_mode=False,
                temperature=0.35,
                max_tokens=300,
            )
            synthesis = resp.get("text", "").strip()
            if synthesis:
                lines.append("")
                lines.append("---")
                lines.append(f"**Grok Synthesis:** {synthesis}")
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
def watchtower_get_social_buzz(ticker: str) -> str:
    """
    Get live X / social media sentiment for a stock via Grok's real-time X access.

    Returns what traders are saying on X right now — sentiment direction,
    buzz level, a 1-sentence summary, and any notable narrative driving chatter.
    This is genuinely real-time even on our delayed data setup since Grok
    queries X directly.
    """
    ticker = ticker.upper().strip()
    try:
        from analysis.social_buzz import query_ticker_sentiment, format_buzz_for_display
        buzz = query_ticker_sentiment(ticker)
        lines = [f"**X / Social Buzz — ${ticker}**", ""]
        lines.append(format_buzz_for_display(ticker, buzz))
        return "\n".join(lines)
    except Exception as e:
        return f"Social buzz unavailable for ${ticker}: {e}"


@mcp.tool()
def watchtower_get_gmmss_context() -> str:
    """Get full current context: regime + top momentum + top bearish ideas + methodology."""
    return "GMMSS context: Bull regime (see current_regime.json in repo). Use watchtower_run_screen or the individual getters for live sleeves. Full synthesis available when all keys (POLYGON, XAI) are configured on Railway."


# ── OAuth 2.0 / PKCE endpoints ────────────────────────────────────────────────

@mcp.tool()
def watchtower_alert_performance(days_back: int = 90, export_csv: bool = False) -> str:
    """
    Show how past Watchtower alerts have performed.

    Tracks every alert that fired over the last `days_back` days and shows
    daily returns at d1, d3, d5, d7, d14, d21, d30 — plus peak return and
    win rates by alert type (intraday, gem, news).

    Args:
        days_back: How far back to look (default 90 days).
        export_csv: If True, returns raw CSV data instead of summary.
    """
    try:
        from analysis.alert_tracker import get_performance_report, generate_csv
        report = get_performance_report(days_back=days_back)

        if "error" in report:
            return f"Error: {report['error']}"

        if export_csv:
            return generate_csv(report)

        lines = [f"**Watchtower Alert Performance — last {days_back} days**", ""]

        total = report.get("total_alerts", 0)
        if total == 0:
            return "No alerts logged yet. Performance tracking starts as soon as the next alert fires."

        lines.append(f"Total alerts tracked: **{total}**")
        lines.append(f"Win threshold: ≥{report['win_threshold_pct']}% gain", )
        lines.append("")

        stats = report.get("stats_by_type", {})
        type_labels = {"intraday": "Intraday Alerts", "gem": "Hidden Gems", "news": "News Catalysts"}

        for at, label in type_labels.items():
            s = stats.get(at)
            if not s:
                continue
            lines.append(f"**{label}** (n={s['n']})")
            if s.get("win_rate_d7") is not None:
                lines.append(f"  Win rate D7:  {s['win_rate_d7']}%")
            if s.get("win_rate_d30") is not None:
                lines.append(f"  Win rate D30: {s['win_rate_d30']}%")
            if s.get("avg_d7_return") is not None:
                lines.append(f"  Avg D7:       {s['avg_d7_return']:+.2f}%")
            if s.get("avg_d30_return") is not None:
                lines.append(f"  Avg D30:      {s['avg_d30_return']:+.2f}%")
            if s.get("avg_peak_return") is not None:
                lines.append(f"  Avg Peak:     {s['avg_peak_return']:+.2f}%")
            if s.get("best_d30") is not None:
                lines.append(f"  Best D30:     {s['best_d30']:+.2f}%")
            if s.get("worst_d30") is not None:
                lines.append(f"  Worst D30:    {s['worst_d30']:+.2f}%")
            lines.append("")

        # Recent alerts table
        rows = report.get("rows", [])[:20]
        if rows:
            lines.append("**Recent Alerts (newest first)**")
            lines.append(f"{'Date':<12} {'Type':<10} {'Ticker':<8} {'Entry':>8} {'D7%':>7} {'D30%':>7} {'Peak%':>7} {'Status':<10}")
            lines.append("-" * 72)
            for r in rows:
                lines.append(
                    f"{r['date']:<12} {r['type']:<10} {r['ticker']:<8} "
                    f"{r['entry']:>8} {r['d7%']:>7} {r['d30%']:>7} {r['peak%']:>7} {r['status']:<10}"
                )

        lines.append("")
        lines.append("Use export_csv=True to get the full dataset.")
        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


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


# ── Scheduler startup ────────────────────────────────────────────────────────

import logging
logging.basicConfig(level=logging.INFO)

try:
    from alerts.scheduler import start_scheduler
    _scheduler = start_scheduler()
except Exception as _sched_err:
    logging.warning(f"[server] Scheduler failed to start (alerts disabled): {_sched_err}")
    _scheduler = None


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
