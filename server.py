"""
Watchtower MCP Server — hosted edition.

Lets Claude.ai chat with the Watchtower stock-finding platform: query the
master screen, dig into individual tickers, manage a watchlist, look up
upcoming earnings, run free-form SQL, etc.

Transport:  Streamable HTTP at /api  (MCP spec compliant)
Auth:       Bearer token (MCP_AUTH_TOKEN env var) + OAuth 2.1/PKCE for
            claude.ai custom-connector registration
Health:     GET /health → 200 OK

Required env vars (configured on Railway):
  MCP_AUTH_TOKEN          — bearer token Claude uses to authenticate
  SUPABASE_DB_HOST/PORT/USER/PASSWORD/NAME — Watchtower Postgres credentials
  PORT                    — set automatically by Railway (default 8000)

Auth scaffolding mirrors the Lumex-FUB MCP pattern so claude.ai's connector
registration flow works identically.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone, timedelta, date
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import uvicorn
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


# ─── Config ───────────────────────────────────────────────────────────────────

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()
PORT           = int(os.environ.get("PORT", 8000))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

if not MCP_AUTH_TOKEN:
    print(json.dumps({"level": "ERROR",
                      "msg": "MCP_AUTH_TOKEN not set — all requests will be rejected"}))
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print(json.dumps({"level": "ERROR",
                      "msg": "SUPABASE_URL or SUPABASE_SERVICE_KEY not set — queries will fail"}))


# ─── Logging ──────────────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            log["request_id"] = record.request_id
        if hasattr(record, "tool"):
            log["tool"] = record.tool
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        return json.dumps(log)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JSONFormatter())
logger = logging.getLogger("watchtower-mcp")
logger.setLevel(logging.INFO)
logger.addHandler(_handler)
logger.propagate = False


# ─── Rate limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._calls: deque = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> tuple[bool, float]:
        async with self._lock:
            now = time.monotonic()
            while self._calls and self._calls[0] < now - self.period:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                oldest = self._calls[0]
                return False, round(self.period - (now - oldest) + 0.1, 2)
            self._calls.append(now)
            return True, 0.0


# 60 req / 10s — generous for an interactive chat session
global_limiter = RateLimiter(60, 10.0)


# ─── DB access (via Supabase PostgREST HTTP API — IPv4, no pooler issues) ────

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _interpolate_params(sql: str, params) -> str:
    """psycopg-style %(name)s parameter substitution. PostgREST RPC takes a
    plain string so we inline the parameters server-side. Safe because
    mcp_exec_sql validates that the resulting query is SELECT/WITH only."""
    if not params:
        return sql
    if isinstance(params, dict):
        out = sql
        for k, v in params.items():
            if v is None:
                rep = "NULL"
            elif isinstance(v, bool):
                rep = "TRUE" if v else "FALSE"
            elif isinstance(v, (int, float)):
                rep = str(v)
            elif isinstance(v, (list, tuple)):
                # Postgres array literal: ARRAY['a','b',1]
                inner = ", ".join(
                    "NULL" if x is None
                    else (str(x) if isinstance(x, (int, float))
                          else "'" + str(x).replace("'", "''") + "'")
                    for x in v
                )
                rep = f"ARRAY[{inner}]"
            elif isinstance(v, (date, datetime)):
                rep = "'" + v.isoformat() + "'"
            else:
                # String — escape single quotes
                rep = "'" + str(v).replace("'", "''") + "'"
            out = out.replace(f"%({k})s", rep)
        return out
    return sql


def _query(sql: str, params=None, fetch: bool = True) -> list[dict]:
    """Run a SELECT/WITH query and return rows as list of dicts.

    Sends the SQL to the mcp_exec_sql Postgres function via PostgREST RPC.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("Supabase URL/key not configured.")
    interpolated = _interpolate_params(sql, params)
    last_err = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30) as client:
                r = client.post(
                    f"{SUPABASE_URL}/rest/v1/rpc/mcp_exec_sql",
                    headers=_sb_headers(),
                    json={"query": interpolated},
                )
            if r.status_code != 200:
                raise RuntimeError(
                    f"PostgREST error {r.status_code}: {r.text[:500]}"
                )
            data = r.json()
            return data if isinstance(data, list) else []
        except (httpx.RequestError, httpx.TimeoutException) as e:
            last_err = e
            time.sleep(2 ** attempt)
            continue
    raise RuntimeError(f"query failed after retries: {last_err}")


def _execute(sql: str, params=None) -> int:
    """Run a write statement via direct PostgREST table operations.

    For INSERT/UPDATE/DELETE we route through PostgREST resource endpoints.
    The watchlist tools are the only writers right now.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("Supabase URL/key not configured.")
    interpolated = _interpolate_params(sql, params)
    lowered = interpolated.lower().strip()

    # Watchlist add: INSERT INTO watchlist ... ON CONFLICT DO UPDATE
    if lowered.startswith("insert into watchlist"):
        # parse the values dict from params
        body = {
            "ticker": params.get("t") if params else None,
            "notes": params.get("n") if params else None,
            "target_price": params.get("tp") if params else None,
        }
        with httpx.Client(timeout=15) as client:
            r = client.post(
                f"{SUPABASE_URL}/rest/v1/watchlist",
                headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=body,
            )
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"watchlist insert error {r.status_code}: {r.text[:300]}")
        return 1

    # Watchlist delete: DELETE FROM watchlist WHERE ticker = ...
    if lowered.startswith("delete from watchlist"):
        ticker = params.get("t") if params else None
        if not ticker:
            return 0
        with httpx.Client(timeout=15) as client:
            r = client.delete(
                f"{SUPABASE_URL}/rest/v1/watchlist",
                headers=_sb_headers(),
                params={"ticker": f"eq.{ticker}"},
            )
        if r.status_code not in (200, 204):
            raise RuntimeError(f"watchlist delete error {r.status_code}: {r.text[:300]}")
        return 1

    raise RuntimeError(f"_execute called with unsupported SQL: {interpolated[:120]}")


# ─── Output formatting helpers ────────────────────────────────────────────────

def _fmt_money(v) -> str:
    if v is None: return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1e9: return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6: return f"${v/1e6:.2f}M"
    if abs(v) >= 1e3: return f"${v/1e3:.1f}K"
    return f"${v:.2f}"


def _fmt_pct(v, decimals: int = 1) -> str:
    if v is None: return "—"
    try:
        return f"{float(v)*100:+.{decimals}f}%"
    except (TypeError, ValueError):
        return str(v)


def _fmt_num(v, decimals: int = 2) -> str:
    if v is None: return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_date(v) -> str:
    if v is None: return "—"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def _none_safe(d: dict, default: dict) -> dict:
    """Merge default into d only for keys missing or None."""
    out = dict(default)
    for k, v in d.items():
        if v is not None:
            out[k] = v
    return out


# ─── OAuth 2.0 (mirrors the Lumex pattern so claude.ai connector flow works) ──

ACCESS_TOKEN_TTL = 60 * 60 * 24   # 24 hours
AUTH_CODE_TTL    = 600            # 10 minutes

_oauth_clients: dict[str, dict] = {}
_oauth_codes:   dict[str, dict] = {}
_oauth_tokens:  dict[str, dict] = {}
_oauth_lock = asyncio.Lock()


def _issuer_for(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{scheme}://{host}".rstrip("/")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    if not verifier or not challenge:
        return False
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    if method == "S256":
        expected = _b64url(hashlib.sha256(verifier.encode()).digest())
        return secrets.compare_digest(expected, challenge)
    return False


def _purge_expired_oauth() -> None:
    now = time.time()
    for d in (_oauth_codes, _oauth_tokens):
        for k in [k for k, v in d.items() if v.get("expires_at", 0) < now]:
            d.pop(k, None)


def _derive_oauth_token(client_id: str) -> str:
    mac = hmac.new(MCP_AUTH_TOKEN.encode(), client_id.encode(), hashlib.sha256).hexdigest()
    b64 = base64.urlsafe_b64encode(client_id.encode()).rstrip(b"=").decode()
    return f"{b64}.{mac}"


def _verify_derived_token(token: str) -> bool:
    try:
        b64, mac = token.rsplit(".", 1)
        padding = (4 - len(b64) % 4) % 4
        client_id = base64.urlsafe_b64decode(b64 + "=" * padding).decode()
        expected = hmac.new(MCP_AUTH_TOKEN.encode(), client_id.encode(), hashlib.sha256).hexdigest()
        return secrets.compare_digest(mac, expected)
    except Exception:
        return False


def _is_valid_oauth_token(token: str) -> bool:
    _purge_expired_oauth()
    return token in _oauth_tokens or _verify_derived_token(token)


def _err_redirect(redirect_uri: str, error: str, state: str = "", desc: str = "") -> Response:
    sep = "&" if "?" in redirect_uri else "?"
    p = {"error": error}
    if state: p["state"] = state
    if desc:  p["error_description"] = desc
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(p)}", status_code=302)


def _authorize_page(client_id, redirect_uri, state, challenge, method, scope,
                    client_name, error=""):
    err_html = f'<div class="error">{error}</div>' if error else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authorize {client_name}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f172a; color: #e2e8f0; max-width: 440px; margin: 80px auto; padding: 32px; }}
h1 {{ font-size: 1.4em; margin: 0 0 8px; color: #38bdf8; }}
p  {{ color: #94a3b8; line-height: 1.5; }}
input[type=password] {{ width: 100%; padding: 12px; font-size: 1em; box-sizing: border-box;
       background: #1e293b; border: 1px solid #334155; color: #e2e8f0; border-radius: 6px; }}
button {{ margin-top: 16px; width: 100%; padding: 12px; font-size: 1em; background: #2563eb;
       color: white; border: none; border-radius: 6px; cursor: pointer; }}
button:hover {{ background: #1d4ed8; }}
.error  {{ color: #f87171; margin: 12px 0; padding: 10px; background: #1f1414; border-radius: 6px; }}
.client {{ background: #1e293b; padding: 12px; border-radius: 6px; margin: 16px 0;
       font-family: monospace; font-size: 0.8em; word-break: break-all; color: #64748b; }}
</style></head><body>
<h1>🔭 Authorize {client_name}</h1>
<p>This Claude session wants access to your Watchtower MCP server. Paste your auth token to approve.</p>
<div class="client">client: {client_id}</div>
{err_html}
<form method="POST">
<input type="hidden" name="client_id" value="{client_id}">
<input type="hidden" name="redirect_uri" value="{redirect_uri}">
<input type="hidden" name="state" value="{state}">
<input type="hidden" name="code_challenge" value="{challenge}">
<input type="hidden" name="code_challenge_method" value="{method}">
<input type="hidden" name="scope" value="{scope}">
<input type="hidden" name="response_type" value="code">
<input type="password" name="auth_token" placeholder="MCP_AUTH_TOKEN" autofocus required>
<button type="submit">Approve</button>
</form>
</body></html>"""


# ─── MCP server ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="watchtower",
    streamable_http_path="/api",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
    instructions=(
        "You are connected to Eric Konoski's Watchtower stock-finding platform. "
        "Data covers ~8,400 US-listed tickers with 14 months of daily prices plus "
        "fundamentals, earnings history, insider transactions, institutional 13F "
        "ownership, short interest, analyst grades, analyst price target revisions, "
        "news sentiment, financial quality scores (Piotroski + Altman), valuation "
        "metrics, and volatility metrics. There's also a master screen that "
        "aggregates all signals into a multi-screen conviction score, plus a "
        "watchlist + alerts system. "
        "When asked about a stock, use screen_detail for the comprehensive view. "
        "For 'what should I buy today?' use master_screen_top. "
        "For free-form analysis you can sql_query against the underlying tables."
    ),
)


# ═════════════════════════════════════════════════════════════════════════════
# TOOL: MASTER SCREEN
# ═════════════════════════════════════════════════════════════════════════════

# Weights mirror screen/master_screen.py so output is consistent
_SCREEN_WEIGHTS = {
    "reversal": 1.0, "value": 1.0, "quality": 0.7, "insider": 1.2,
    "institutional": 1.2, "short_squeeze": 0.8, "revisions": 0.9,
    "grades": 1.0, "news": 0.7, "earnings_beat": 0.9,
}
_SIGNAL_THRESHOLD = 60.0


def _load_master_signals() -> dict[str, dict]:
    """Build per-ticker signal map from all persisted tables."""
    signals: dict[str, dict] = {}

    # Valuation metrics → "value" 0-100
    for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, composite_value_score, pe, ev_ebitda, fcf_yield
        FROM valuation_metrics ORDER BY ticker, as_of_date DESC
    """):
        t = r["ticker"]
        signals.setdefault(t, {})
        v = r.get("composite_value_score")
        signals[t]["value"] = float(v) if v is not None else None
        signals[t]["pe"] = float(r["pe"]) if r.get("pe") is not None else None

    # Financial scores → "quality" 0-100
    for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, piotroski_score, altman_z_score
        FROM financial_scores ORDER BY ticker, as_of_date DESC
    """):
        t = r["ticker"]
        p = r.get("piotroski_score")
        z = r.get("altman_z_score")
        q = 0.0
        if p is not None:
            q += float(p) * 60.0 / 9.0
        if z is not None:
            z_f = float(z)
            if z_f >= 2.99: q += 40
            elif z_f >= 1.81: q += 20
        signals.setdefault(t, {})
        signals[t]["quality"] = q
        signals[t]["piotroski"] = int(p) if p is not None else None
        signals[t]["altman_z"] = float(z) if z is not None else None

    # Insider buying intensity
    for r in _query("""
        WITH recent AS (
          SELECT ticker,
                 SUM(total_purchases - total_sales) FILTER (WHERE qrank <= 2) AS net,
                 SUM(total_purchases) FILTER (WHERE qrank <= 2) AS buys
          FROM (
            SELECT ticker, total_purchases, total_sales,
                   ROW_NUMBER() OVER (PARTITION BY ticker
                                      ORDER BY fiscal_year DESC, fiscal_quarter DESC) AS qrank
            FROM insider_stats
          ) r GROUP BY ticker
        )
        SELECT * FROM recent WHERE net > 0
    """):
        t = r["ticker"]
        net = int(r["net"] or 0)
        score = 0.0
        if net >= 10: score = 80
        elif net >= 5: score = 56
        elif net >= 3: score = 35
        elif net >= 1: score = 18
        signals.setdefault(t, {})["insider"] = score
        signals[t]["insider_net"] = net

    # Short interest → squeeze
    for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, short_percent_of_float, short_ratio
        FROM short_interest ORDER BY ticker, as_of_date DESC
    """):
        t = r["ticker"]
        pct = float(r["short_percent_of_float"]) * 100 if r["short_percent_of_float"] else 0
        dtc = float(r["short_ratio"]) if r["short_ratio"] else 0
        score = 0.0
        if pct >= 25 and dtc >= 5: score = 80
        elif pct >= 20 and dtc >= 4: score = 65
        elif pct >= 15 and dtc >= 3: score = 50
        elif pct >= 10: score = 30
        signals.setdefault(t, {})["short_squeeze"] = score
        signals[t]["short_pct"] = pct

    # Analyst revisions
    for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, revision_30d_pct, upside_to_target_pct,
               pt_last_month_count
        FROM analyst_revisions ORDER BY ticker, as_of_date DESC
    """):
        t = r["ticker"]
        rev = float(r["revision_30d_pct"]) if r["revision_30d_pct"] else 0
        ups = float(r["upside_to_target_pct"]) if r["upside_to_target_pct"] else 0
        n = int(r["pt_last_month_count"] or 0)
        score = 0.0
        if rev >= 0.10: score += 40
        elif rev >= 0.05: score += 28
        elif rev >= 0.02: score += 15
        if ups >= 0.30: score += 30
        elif ups >= 0.15: score += 18
        elif ups >= 0.05: score += 8
        if n >= 10: score += 20
        elif n >= 5: score += 12
        signals.setdefault(t, {})["revisions"] = min(100.0, score)
        signals[t]["upside_pct"] = ups

    # Analyst grades
    for r in _query("""
        WITH a30 AS (
          SELECT ticker,
                 COUNT(*) FILTER (WHERE action = 'upgrade') AS up,
                 COUNT(*) FILTER (WHERE action = 'downgrade') AS down
          FROM analyst_grades
          WHERE event_date >= current_date - interval '30 days'
          GROUP BY ticker
        )
        SELECT ticker, COALESCE(up, 0) AS up30, COALESCE(down, 0) AS down30 FROM a30
    """):
        t = r["ticker"]
        net30 = int(r["up30"]) - int(r["down30"])
        score = 0.0
        if net30 >= 5: score = 70
        elif net30 >= 3: score = 50
        elif net30 >= 1: score = 25
        elif net30 <= -3: score = 0
        signals.setdefault(t, {})["grades"] = max(0.0, score)
        signals[t]["net_upgrades_30d"] = net30

    # Institutional
    for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, holders_increasing, holders_decreasing,
               holders_new, weighted_change_pct, institutions_pct
        FROM institutional_ownership ORDER BY ticker, as_of_date DESC
    """):
        t = r["ticker"]
        inc = int(r["holders_increasing"] or 0)
        dec = int(r["holders_decreasing"] or 0)
        new = int(r["holders_new"] or 0)
        wc = float(r["weighted_change_pct"] or 0)
        ip = float(r["institutions_pct"] or 0)
        net = inc - dec
        score = 0.0
        if net >= 6: score += 40
        elif net >= 4: score += 30
        elif net >= 2: score += 18
        if new >= 3: score += 20
        elif new >= 1: score += 8
        if wc >= 0.30: score += 25
        elif wc >= 0.10: score += 15
        if 0.30 <= ip <= 0.80: score += 15
        signals.setdefault(t, {})["institutional"] = min(100.0, score)
        signals[t]["inst_net_holders"] = net

    # News sentiment
    for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, sentiment_7d, sentiment_change_pct, n_articles_7d
        FROM news_sentiment ORDER BY ticker, as_of_date DESC
    """):
        t = r["ticker"]
        s7 = float(r["sentiment_7d"] or 0)
        chg = float(r["sentiment_change_pct"] or 0)
        n7 = int(r["n_articles_7d"] or 0)
        if n7 < 3:
            continue
        score = 0.0
        if s7 >= 1.5: score += 35
        elif s7 >= 1.0: score += 25
        elif s7 >= 0.5: score += 15
        if chg >= 1.0: score += 30
        elif chg >= 0.5: score += 20
        if n7 >= 10: score += 15
        elif n7 >= 5: score += 8
        signals.setdefault(t, {})["news"] = min(100.0, score)
        signals[t]["sentiment_7d"] = s7

    # Recent earnings beats
    for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, surprise_pct
        FROM earnings_history
        WHERE eps_actual IS NOT NULL
          AND report_date >= current_date - interval '60 days'
          AND surprise_pct IS NOT NULL
        ORDER BY ticker, report_date DESC
    """):
        t = r["ticker"]
        sur = float(r["surprise_pct"] or 0)
        score = 0.0
        if sur >= 0.20: score = 80
        elif sur >= 0.10: score = 65
        elif sur >= 0.05: score = 50
        elif sur >= 0.02: score = 30
        signals.setdefault(t, {})["earnings_beat"] = score

    # Upcoming earnings flag (informational, not scored)
    for r in _query("""
        SELECT ticker, (report_date - current_date) AS days_to
        FROM earnings_calendar
        WHERE report_date BETWEEN current_date AND current_date + interval '14 days'
    """):
        signals.setdefault(r["ticker"], {})["earnings_in_days"] = int(r["days_to"])

    # Volatility regime
    for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, vol_regime, atr_pct_price, beta_vs_spy
        FROM volatility_metrics ORDER BY ticker, as_of_date DESC
    """):
        t = r["ticker"]
        signals.setdefault(t, {})
        signals[t]["vol_regime"] = r.get("vol_regime")
        signals[t]["beta"] = float(r["beta_vs_spy"]) if r.get("beta_vs_spy") else None

    return signals


def _compute_master_score(sig: dict) -> dict:
    contributing = {}
    for key in _SCREEN_WEIGHTS:
        v = sig.get(key)
        if v is not None:
            contributing[key] = v
    if not contributing:
        return {"total_score": 0.0, "n_signals": 0, "n_strong": 0, "by_screen": {}}
    total_weight = sum(_SCREEN_WEIGHTS[k] for k in contributing)
    weighted_sum = sum(_SCREEN_WEIGHTS[k] * v for k, v in contributing.items())
    base = weighted_sum / total_weight if total_weight > 0 else 0
    n_strong = sum(1 for v in contributing.values() if v >= _SIGNAL_THRESHOLD)
    bonus = 0.0
    if n_strong >= 5: bonus = 12
    elif n_strong >= 4: bonus = 8
    elif n_strong >= 3: bonus = 4

    # Value-trap penalty
    p = sig.get("piotroski")
    z = sig.get("altman_z")
    if p is not None and z is not None and p <= 3 and z < 1.81:
        base *= 0.65

    return {
        "total_score": min(100.0, base + bonus),
        "n_signals": len(contributing),
        "n_strong": n_strong,
        "by_screen": contributing,
    }


@mcp.tool()
def master_screen_top(top: int = 25, min_screens: int = 3,
                      sector: str = "", max_price: float = 0) -> str:
    """Top stocks ranked by the master screen — combines all 12 signal sources
    into one conviction score per ticker. Returns a ranked list with per-screen
    breakdown showing where each stock is strong.

    Args:
      top: how many results to return (default 25)
      min_screens: minimum number of signals required (default 3)
      sector: optional sector filter (e.g., 'Technology', 'Healthcare')
      max_price: optional max share price (0 = no cap)
    """
    signals = _load_master_signals()
    # Latest price + sector/company
    prices = {r["ticker"]: float(r["close"]) for r in _query("""
        SELECT DISTINCT ON (ticker) ticker, close
        FROM daily_prices ORDER BY ticker, trade_date DESC
    """)}
    meta = {r["ticker"]: r for r in _query("""
        SELECT ticker, company_name, sector FROM tickers
        WHERE delisted = false AND country = 'US'
    """)}

    out = []
    for ticker, sig in signals.items():
        m = meta.get(ticker)
        if not m:
            continue
        if sector and (m.get("sector") or "").lower() != sector.lower():
            continue
        price = prices.get(ticker)
        if price is None:
            continue
        if max_price > 0 and price > max_price:
            continue
        score = _compute_master_score(sig)
        if score["n_signals"] < min_screens:
            continue
        out.append({
            "ticker": ticker,
            "company": m.get("company_name") or "",
            "sector": m.get("sector") or "",
            "price": price,
            "score": score["total_score"],
            "n_strong": score["n_strong"],
            "n_signals": score["n_signals"],
            "by_screen": score["by_screen"],
            "earnings_in_days": sig.get("earnings_in_days"),
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    out = out[:top]

    if not out:
        return "No candidates matched the filters."

    lines = [
        f"MASTER SCREEN — top {len(out)} (≥{min_screens} signals required)",
        ""
    ]
    for r in out:
        breakdown = " ".join(
            f"{k[:3].upper()}:{int(v)}" for k, v in r["by_screen"].items() if v >= 50
        )
        flag = f" ⏰{r['earnings_in_days']}d" if r.get("earnings_in_days") is not None else ""
        lines.append(
            f"  {r['ticker']:<7} {r['company'][:30]:<30} "
            f"{r['sector'][:18]:<18} ${r['price']:>7.2f}  "
            f"{r['n_strong']}/{r['n_signals']} strong  "
            f"score {r['score']:>4.0f}{flag}"
        )
        if breakdown:
            lines.append(f"          {breakdown}")
    return "\n".join(lines)


@mcp.tool()
def screen_detail(ticker: str) -> str:
    """Full breakdown for one ticker across all 12 underlying screens.
    Shows per-screen scores, fundamental quality, recent insider/institutional
    activity, news sentiment, technical context, upcoming earnings, vol regime.

    Args:
      ticker: the ticker symbol (e.g., 'NKE', 'DECK')
    """
    ticker = ticker.upper()
    signals_all = _load_master_signals()
    sig = signals_all.get(ticker, {})
    score = _compute_master_score(sig)

    meta = _query(
        "SELECT ticker, company_name, sector, industry FROM tickers WHERE ticker = %(t)s",
        {"t": ticker},
    )
    if not meta:
        return f"No data for {ticker}."
    m = meta[0]
    price_rows = _query(
        "SELECT close, trade_date FROM daily_prices WHERE ticker = %(t)s "
        "ORDER BY trade_date DESC LIMIT 1",
        {"t": ticker},
    )
    price = float(price_rows[0]["close"]) if price_rows else None
    hi_rows = _query(
        "SELECT MAX(close) AS hi FROM daily_prices WHERE ticker = %(t)s "
        "AND trade_date >= current_date - interval '365 days'",
        {"t": ticker},
    )
    hi52 = float(hi_rows[0]["hi"]) if hi_rows and hi_rows[0].get("hi") else None
    pct_off = (1 - price / hi52) * 100 if (price and hi52) else None

    lines = [
        f"═══ {ticker} — {m.get('company_name') or ''} ═══",
        f"  Sector:   {m.get('sector') or '—'}",
        f"  Industry: {m.get('industry') or '—'}",
    ]
    if price is not None:
        lines.append(f"  Current price: ${price:.2f}")
    if hi52 is not None:
        lines.append(f"  52w high:      ${hi52:.2f}")
    if pct_off is not None:
        lines.append(f"  Drawdown:      {pct_off:.1f}%")

    lines.append("\nPER-SCREEN SCORES (0-100):")
    labels = [
        ("reversal", "Reversal (tech, beaten-down)"),
        ("value", "Deep value (cheap vs sector)"),
        ("quality", "Quality (Piotroski + Altman)"),
        ("insider", "Insider buying"),
        ("institutional", "Institutional accumulation"),
        ("short_squeeze", "Short squeeze setup"),
        ("revisions", "Analyst PT revisions"),
        ("grades", "Analyst upgrades"),
        ("news", "News sentiment"),
        ("earnings_beat", "Recent earnings beat"),
    ]
    for key, label in labels:
        v = sig.get(key)
        if v is None:
            lines.append(f"  {label:<32}  —  (no data)")
        else:
            tag = " ◀ STRONG" if v >= _SIGNAL_THRESHOLD else ""
            lines.append(f"  {label:<32}  {v:>5.0f}{tag}")

    lines.append(f"\n  Consensus: {score['n_strong']} of {score['n_signals']} screens scored ≥{int(_SIGNAL_THRESHOLD)}")
    lines.append(f"  TOTAL MASTER SCORE: {score['total_score']:.0f} / 100")

    lines.append("\nFUNDAMENTAL CONTEXT:")
    if sig.get("piotroski") is not None:
        lines.append(f"  Piotroski F:   {sig['piotroski']}/9")
    if sig.get("altman_z") is not None:
        z = float(sig["altman_z"])
        zone = "safe" if z >= 2.99 else "gray" if z >= 1.81 else "DISTRESS ⚠"
        lines.append(f"  Altman Z:      {z:.2f}  ({zone})")
    if sig.get("pe") is not None and sig["pe"] > 0:
        lines.append(f"  P/E:           {sig['pe']:.1f}")
    if sig.get("sentiment_7d") is not None:
        lines.append(f"  Sentiment 7d:  {sig['sentiment_7d']:+.2f}")
    if sig.get("net_upgrades_30d") is not None:
        lines.append(f"  Net upgrades 30d: {sig['net_upgrades_30d']:+d}")
    if sig.get("insider_net") is not None:
        lines.append(f"  Insider net buys: {sig['insider_net']:+d}")
    if sig.get("inst_net_holders") is not None:
        lines.append(f"  Inst net holders: {sig['inst_net_holders']:+d}")
    if sig.get("short_pct") is not None and sig["short_pct"] >= 5:
        lines.append(f"  Short % of float: {sig['short_pct']:.1f}%")
    if sig.get("vol_regime"):
        lines.append(f"  Vol regime:    {sig['vol_regime']}")
    if sig.get("beta") is not None:
        lines.append(f"  Beta vs SPY:   {sig['beta']:.2f}")
    if sig.get("earnings_in_days") is not None:
        lines.append(f"  ⏰ Earnings:    in {sig['earnings_in_days']} days")
    if sig.get("upside_pct") is not None:
        lines.append(f"  Upside to PT:  {sig['upside_pct']*100:+.1f}%")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# WATCHLIST TOOLS
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def watchlist_list() -> str:
    """Show current watchlist with notes, target prices, and most recent price."""
    rows = _query("""
        SELECT w.ticker, w.added_at, w.notes, w.target_price,
               t.company_name, t.sector,
               (SELECT close FROM daily_prices WHERE ticker = w.ticker
                ORDER BY trade_date DESC LIMIT 1) AS current_price
        FROM watchlist w
        LEFT JOIN tickers t ON t.ticker = w.ticker
        ORDER BY w.added_at DESC
    """)
    if not rows:
        return "Watchlist is empty. Use watchlist_add to add tickers."
    lines = [f"WATCHLIST ({len(rows)} tickers):", ""]
    for r in rows:
        price = r.get("current_price")
        target = r.get("target_price")
        price_s = f"${float(price):.2f}" if price is not None else "—"
        target_s = f"${float(target):.2f}" if target is not None else "—"
        gap = ""
        if price is not None and target is not None and float(target) > 0:
            gap = f"  ({(float(price)/float(target) - 1)*100:+.1f}% vs target)"
        lines.append(
            f"  {r['ticker']:<7} {r.get('company_name') or '—':<28} "
            f"price {price_s:>8}  target {target_s:>8}{gap}"
        )
        if r.get("notes"):
            lines.append(f"          notes: {r['notes']}")
    return "\n".join(lines)


@mcp.tool()
def watchlist_add(ticker: str, notes: str = "", target_price: float = 0) -> str:
    """Add (or update) a ticker on the watchlist. The daily refresh will check
    it for events (new STRONG BUY, EMA cross, earnings approaching, etc.).

    Args:
      ticker: symbol to watch (will be uppercased)
      notes: optional personal notes
      target_price: optional take-profit target. 0 = no target.
    """
    ticker = ticker.upper()
    _execute("""
        INSERT INTO watchlist (ticker, notes, target_price)
        VALUES (%(t)s, %(n)s, %(tp)s)
        ON CONFLICT (ticker) DO UPDATE SET
          notes = EXCLUDED.notes,
          target_price = EXCLUDED.target_price
    """, {"t": ticker, "n": notes or None, "tp": target_price or None})
    return f"Added {ticker} to watchlist."


@mcp.tool()
def watchlist_remove(ticker: str) -> str:
    """Remove a ticker from the watchlist."""
    ticker = ticker.upper()
    n = _execute("DELETE FROM watchlist WHERE ticker = %(t)s", {"t": ticker})
    return f"Removed {ticker}." if n else f"{ticker} was not on the watchlist."


@mcp.tool()
def watchlist_alerts(days_back: int = 7, ticker: str = "") -> str:
    """Show recent watchlist alerts. Each alert is generated by the daily
    refresh when a watched ticker crosses a threshold (new STRONG BUY,
    earnings approaching, sentiment collapse, EMA cross, etc.).

    Args:
      days_back: how many days of history to include (default 7)
      ticker: optional — filter to one ticker
    """
    sql = """
        SELECT wa.ticker, wa.alert_date, wa.alert_type, wa.severity, wa.message,
               t.company_name
        FROM watchlist_alerts wa
        LEFT JOIN tickers t ON t.ticker = wa.ticker
        WHERE wa.alert_date >= current_date - %(d)s * interval '1 day'
    """
    params = {"d": days_back}
    if ticker:
        sql += " AND wa.ticker = %(t)s"
        params["t"] = ticker.upper()
    sql += " ORDER BY wa.alert_date DESC, wa.severity"
    rows = _query(sql, params)
    if not rows:
        return "No alerts in window."
    lines = [f"ALERTS — last {days_back} days ({len(rows)} events):", ""]
    for r in rows:
        co = (r.get("company_name") or "")[:24]
        lines.append(
            f"  {r['alert_date']} [{r['severity']:<5}] {r['ticker']:<6} "
            f"{co:<24} {r['alert_type']:<22} {r['message']}"
        )
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# COMPARISON + EXPLORATION TOOLS
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def compare_tickers(tickers: str) -> str:
    """Side-by-side comparison of multiple tickers across all screens.

    Args:
      tickers: comma-separated list (e.g., 'NKE,DECK,GAP')
    """
    syms = [s.strip().upper() for s in tickers.split(",") if s.strip()]
    if not syms:
        return "Provide a comma-separated list of tickers."
    if len(syms) > 8:
        return "Maximum 8 tickers per comparison."

    signals_all = _load_master_signals()
    meta = {r["ticker"]: r for r in _query(
        "SELECT ticker, company_name, sector FROM tickers WHERE ticker = ANY(%(s)s)",
        {"s": syms},
    )}
    prices = {r["ticker"]: float(r["close"]) for r in _query(
        "SELECT DISTINCT ON (ticker) ticker, close FROM daily_prices "
        "WHERE ticker = ANY(%(s)s) ORDER BY ticker, trade_date DESC",
        {"s": syms},
    )}

    rows = []
    for t in syms:
        sig = signals_all.get(t, {})
        score = _compute_master_score(sig)
        rows.append({
            "ticker": t,
            "company": (meta.get(t) or {}).get("company_name") or "—",
            "sector": (meta.get(t) or {}).get("sector") or "—",
            "price": prices.get(t),
            "total": score["total_score"],
            "n_strong": score["n_strong"],
            "by_screen": score["by_screen"],
            "earnings_in_days": sig.get("earnings_in_days"),
        })

    lines = [f"COMPARISON ({len(syms)} tickers):", ""]
    # Header
    screens = ["reversal", "value", "quality", "insider", "institutional",
               "short_squeeze", "revisions", "grades", "news", "earnings_beat"]
    short_labels = ["REV", "VAL", "QLY", "INS", "INST", "SQZ", "RVN", "GRD", "NWS", "ERN"]
    hdr = f"  {'TICKER':<7} {'PRICE':>8} " + " ".join(f"{lbl:>4}" for lbl in short_labels) + f"  {'TOT':>4}"
    lines.append(hdr)
    lines.append("  " + "─" * (len(hdr) - 2))
    for r in rows:
        price_s = f"${r['price']:.2f}" if r["price"] is not None else "—"
        cells = " ".join(
            f"{int(r['by_screen'].get(k, 0)):>4}" if r["by_screen"].get(k) is not None else "   ·"
            for k in screens
        )
        flag = f" ⏰{r['earnings_in_days']}d" if r.get("earnings_in_days") is not None else ""
        lines.append(f"  {r['ticker']:<7} {price_s:>8} {cells}  {r['total']:>4.0f}{flag}")
        lines.append(f"          {r['company'][:50]}")
    return "\n".join(lines)


@mcp.tool()
def sector_summary(sector: str, top: int = 15) -> str:
    """Top stocks in a sector ranked by master screen score.

    Args:
      sector: e.g., 'Technology', 'Healthcare', 'Consumer Cyclical', 'Industrials'
      top: how many to show (default 15)
    """
    return master_screen_top(top=top, min_screens=2, sector=sector)


# ═════════════════════════════════════════════════════════════════════════════
# CATALYST + EVENT TOOLS
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def upcoming_earnings(days: int = 14, min_trailing_surprise: float = 0) -> str:
    """Stocks reporting earnings in the next N days, sorted by trailing
    4-quarter average surprise (highest beats first).

    Args:
      days: days forward to look (default 14)
      min_trailing_surprise: filter to stocks with trailing avg ≥ this fraction
                             (e.g., 0.05 = 5%+ historical beats). 0 = no filter.
    """
    rows = _query("""
        SELECT ec.ticker, ec.report_date, ec.time_of_day, ec.eps_estimated,
               ec.last_4q_surprise_avg, t.company_name, t.sector
        FROM earnings_calendar ec
        JOIN tickers t ON t.ticker = ec.ticker
        WHERE ec.report_date BETWEEN current_date AND current_date + %(d)s * interval '1 day'
          AND (%(ms)s = 0 OR ec.last_4q_surprise_avg >= %(ms)s)
        ORDER BY ec.report_date ASC, ec.last_4q_surprise_avg DESC NULLS LAST
        LIMIT 40
    """, {"d": days, "ms": min_trailing_surprise})
    if not rows:
        return "No upcoming earnings in window."
    lines = [f"UPCOMING EARNINGS — next {days} days ({len(rows)} events):", ""]
    for r in rows:
        eps = r.get("eps_estimated")
        eps_s = f"${float(eps):.2f}" if eps is not None else "  N/A"
        sur = r.get("last_4q_surprise_avg")
        sur_s = f"{float(sur)*100:+.1f}%" if sur is not None else "   N/A"
        time_s = (r.get("time_of_day") or "")[:4]
        co = (r.get("company_name") or "")[:25]
        lines.append(
            f"  {r['report_date']}  {time_s:<4}  {r['ticker']:<7} "
            f"EPS_est {eps_s:>7}  4Q avg {sur_s}  {co}"
        )
    return "\n".join(lines)


@mcp.tool()
def recent_earnings_beats(days_back: int = 14, min_surprise: float = 0.05) -> str:
    """Stocks that reported significant EPS beats in the recent past.

    Args:
      days_back: lookback window in days (default 14)
      min_surprise: minimum surprise as fraction (default 0.05 = 5%+)
    """
    rows = _query("""
        SELECT eh.ticker, eh.report_date, eh.eps_actual, eh.eps_estimated,
               eh.surprise_pct, t.company_name, t.sector,
               (SELECT close FROM daily_prices WHERE ticker = eh.ticker
                ORDER BY trade_date DESC LIMIT 1) AS current_price
        FROM earnings_history eh
        JOIN tickers t ON t.ticker = eh.ticker
        WHERE eh.eps_actual IS NOT NULL
          AND eh.report_date >= current_date - %(d)s * interval '1 day'
          AND eh.surprise_pct >= %(ms)s
        ORDER BY eh.report_date DESC, eh.surprise_pct DESC
        LIMIT 40
    """, {"d": days_back, "ms": min_surprise})
    if not rows:
        return "No recent beats matching filters."
    lines = [f"RECENT EARNINGS BEATS — last {days_back} days, ≥{min_surprise*100:.0f}% beat:", ""]
    for r in rows:
        co = (r.get("company_name") or "")[:25]
        price = r.get("current_price")
        price_s = f"${float(price):.2f}" if price is not None else "—"
        lines.append(
            f"  {r['report_date']}  {r['ticker']:<7} "
            f"surprise {float(r['surprise_pct'])*100:>+5.1f}%  price {price_s:>8}  {co}"
        )
    return "\n".join(lines)


@mcp.tool()
def recent_insider_activity(days_back: int = 90, min_net_buys: int = 3) -> str:
    """Stocks with strong recent insider buying (net purchases this quarter).

    Args:
      days_back: lookback (informational — quarterly data, max ~90)
      min_net_buys: minimum net buys (purchases − sales) in most recent quarter
    """
    rows = _query("""
        WITH recent AS (
          SELECT DISTINCT ON (ticker) ticker, total_purchases, total_sales,
                 fiscal_year, fiscal_quarter
          FROM insider_stats
          ORDER BY ticker, fiscal_year DESC, fiscal_quarter DESC
        )
        SELECT r.ticker, t.company_name, t.sector,
               r.total_purchases, r.total_sales,
               r.fiscal_year, r.fiscal_quarter,
               (SELECT close FROM daily_prices WHERE ticker = r.ticker
                ORDER BY trade_date DESC LIMIT 1) AS price
        FROM recent r
        JOIN tickers t ON t.ticker = r.ticker
        WHERE (r.total_purchases - r.total_sales) >= %(mn)s
        ORDER BY (r.total_purchases - r.total_sales) DESC
        LIMIT 30
    """, {"mn": min_net_buys})
    if not rows:
        return "No insider activity matching filters."
    lines = [f"INSIDER BUYING (most recent quarter, net ≥{min_net_buys}):", ""]
    for r in rows:
        buys = int(r["total_purchases"] or 0)
        sells = int(r["total_sales"] or 0)
        co = (r.get("company_name") or "")[:25]
        price = r.get("price")
        price_s = f"${float(price):.2f}" if price is not None else "—"
        lines.append(
            f"  {r['ticker']:<7} {r['fiscal_year']}Q{r['fiscal_quarter']}  "
            f"net +{buys - sells:>3} ({buys} buys / {sells} sells)  "
            f"price {price_s:>8}  {co}"
        )
    return "\n".join(lines)


@mcp.tool()
def institutional_accumulation(min_net_holders: int = 4) -> str:
    """Stocks where institutional investors (top-10 13F holders) are
    accumulating shares q/q.

    Args:
      min_net_holders: min (increasing − decreasing) among top 10
    """
    rows = _query("""
        SELECT DISTINCT ON (i.ticker) i.ticker, i.holders_increasing,
               i.holders_decreasing, i.holders_new, i.weighted_change_pct,
               i.institutions_pct, t.company_name, t.sector,
               (SELECT close FROM daily_prices WHERE ticker = i.ticker
                ORDER BY trade_date DESC LIMIT 1) AS price
        FROM institutional_ownership i
        JOIN tickers t ON t.ticker = i.ticker
        WHERE (i.holders_increasing - i.holders_decreasing) >= %(mn)s
        ORDER BY i.ticker, i.as_of_date DESC, i.weighted_change_pct DESC
        LIMIT 30
    """, {"mn": min_net_holders})
    if not rows:
        return "No institutional accumulation matching filters."
    lines = [f"INSTITUTIONAL ACCUMULATION (net ≥{min_net_holders}):", ""]
    for r in rows:
        co = (r.get("company_name") or "")[:25]
        wc = float(r["weighted_change_pct"] or 0) * 100
        ip = float(r["institutions_pct"] or 0) * 100
        net = int(r["holders_increasing"] or 0) - int(r["holders_decreasing"] or 0)
        new = int(r["holders_new"] or 0)
        price = r.get("price")
        price_s = f"${float(price):.2f}" if price is not None else "—"
        lines.append(
            f"  {r['ticker']:<7} net +{net}  new {new}  wChg {wc:+6.1f}%  "
            f"inst {ip:>4.0f}%  price {price_s:>8}  {co}"
        )
    return "\n".join(lines)


@mcp.tool()
def analyst_grade_changes(days: int = 30, top: int = 25) -> str:
    """Stocks with recent net analyst upgrades.

    Args:
      days: lookback window (default 30)
      top: how many to show
    """
    rows = _query("""
        WITH agg AS (
          SELECT ticker,
                 COUNT(*) FILTER (WHERE action = 'upgrade') AS up,
                 COUNT(*) FILTER (WHERE action = 'downgrade') AS down
          FROM analyst_grades
          WHERE event_date >= current_date - %(d)s * interval '1 day'
          GROUP BY ticker
        )
        SELECT a.ticker, a.up, a.down, (a.up - a.down) AS net,
               t.company_name, t.sector,
               (SELECT close FROM daily_prices WHERE ticker = a.ticker
                ORDER BY trade_date DESC LIMIT 1) AS price
        FROM agg a
        JOIN tickers t ON t.ticker = a.ticker
        WHERE (a.up - a.down) >= 1
        ORDER BY net DESC LIMIT %(top)s
    """, {"d": days, "top": top})
    if not rows:
        return "No net upgrades in window."
    lines = [f"NET ANALYST UPGRADES — last {days} days:", ""]
    for r in rows:
        co = (r.get("company_name") or "")[:25]
        price = r.get("price")
        price_s = f"${float(price):.2f}" if price is not None else "—"
        lines.append(
            f"  {r['ticker']:<7} net +{r['net']:>3}  "
            f"({r['up']} up / {r['down']} down)  "
            f"price {price_s:>8}  {co}"
        )
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# BACKTEST TOOLS
# ═════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def backtest_summary() -> str:
    """Recent backtest runs — compare strategies by win rate, avg return,
    and excess return vs SPY."""
    rows = _query("""
        SELECT * FROM backtest_runs ORDER BY run_at DESC LIMIT 20
    """)
    if not rows:
        return "No backtest runs yet."
    lines = ["BACKTEST RUNS:", ""]
    lines.append(f"  {'STRATEGY':<26} {'PICKS':>5} {'WR3M':>5} {'AVG3M':>6} "
                 f"{'EXCESS':>6} {'SHARPE':>7}  RUN_ID")
    lines.append(f"  {'─' * 26} {'─' * 5} {'─' * 5} {'─' * 6} "
                 f"{'─' * 6} {'─' * 7}  ─────")
    for r in rows:
        wr = float(r["win_rate_3m"]) * 100 if r["win_rate_3m"] else 0
        avg = float(r["avg_return_3m"]) * 100 if r["avg_return_3m"] else 0
        ex = float(r["excess_return_3m"]) * 100 if r["excess_return_3m"] else 0
        sh = float(r["sharpe_proxy"]) if r["sharpe_proxy"] else 0
        lines.append(
            f"  {r['strategy_name']:<26} {r['n_picks']:>5} {wr:>4.0f}% "
            f"{avg:>+5.1f}% {ex:>+5.1f}% {sh:>7.3f}  {r['run_id']}"
        )
    return "\n".join(lines)


@mcp.tool()
def backtest_top_picks(run_id: int = 0, top: int = 10, mode: str = "winners") -> str:
    """Show top winners or losers from a specific backtest run.

    Args:
      run_id: run id from backtest_summary. 0 = most recent run.
      top: how many to show
      mode: 'winners' (highest 3m returns) or 'losers' (lowest)
    """
    if run_id <= 0:
        latest = _query("SELECT MAX(run_id) AS m FROM backtest_runs")
        if not latest or latest[0].get("m") is None:
            return "No backtest runs yet."
        run_id = latest[0]["m"]
    order = "DESC" if mode != "losers" else "ASC"
    rows = _query(f"""
        SELECT bp.*, t.company_name
        FROM backtest_picks bp
        LEFT JOIN tickers t ON t.ticker = bp.ticker
        WHERE bp.run_id = %(r)s AND bp.return_3m_pct IS NOT NULL
        ORDER BY bp.return_3m_pct {order} LIMIT %(top)s
    """, {"r": run_id, "top": top})
    if not rows:
        return f"No picks for run {run_id}."
    lines = [f"BACKTEST PICKS (run {run_id}, top {top} {mode}):", ""]
    for r in rows:
        ret = float(r["return_3m_pct"]) * 100 if r["return_3m_pct"] is not None else 0
        ex = float(r["excess_return_3m_pct"]) * 100 if r["excess_return_3m_pct"] else 0
        ent = float(r["entry_price"]) if r["entry_price"] else 0
        co = (r.get("company_name") or "")[:25]
        lines.append(
            f"  {r['ticker']:<7} {r['as_of_date']}  entry ${ent:>7.2f}  "
            f"3m {ret:>+6.1f}%  excess vs SPY {ex:>+6.1f}%  {co}"
        )
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# POWER TOOL: SQL query (read-only)
# ═════════════════════════════════════════════════════════════════════════════

_FORBIDDEN_SQL = {"insert", "update", "delete", "drop", "alter", "truncate",
                  "create", "grant", "revoke", "commit", "rollback"}


@mcp.tool()
def sql_query(query: str, limit: int = 50) -> str:
    """Run a read-only SELECT query against the Watchtower database. Useful
    for ad-hoc analysis the predefined tools don't cover.

    Args:
      query: a SELECT statement (other statement types are rejected)
      limit: max rows to return (caps at 500)

    Available tables:
      tickers, daily_prices, fundamentals_quarterly, earnings_history,
      earnings_calendar, insider_stats, institutional_ownership,
      short_interest, analyst_revisions, analyst_grades, news_sentiment,
      financial_scores, valuation_metrics, volatility_metrics,
      watchlist, watchlist_alerts, backtest_runs, backtest_picks
    """
    q = query.strip().rstrip(";")
    lowered = q.lower()
    # Reject obvious mutations
    for kw in _FORBIDDEN_SQL:
        if f" {kw} " in f" {lowered} " or lowered.startswith(kw + " "):
            return f"Forbidden keyword: '{kw}'. Only SELECT queries allowed."
    if not lowered.startswith("select") and not lowered.startswith("with"):
        return "Query must start with SELECT or WITH."

    limit = min(max(1, limit), 500)
    # Wrap in subquery to enforce limit
    wrapped = f"SELECT * FROM ({q}) AS _user_q LIMIT {limit}"
    try:
        rows = _query(wrapped)
    except Exception as e:
        return f"Query error: {type(e).__name__}: {str(e)[:300]}"

    if not rows:
        return "(no rows)"
    # Format as text table
    cols = list(rows[0].keys())
    # Compute widths
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    for c in widths:
        widths[c] = min(widths[c], 30)
    lines = [
        " | ".join(c.ljust(widths[c])[:widths[c]] for c in cols),
        " | ".join("-" * widths[c] for c in cols),
    ]
    for r in rows:
        lines.append(" | ".join(
            (str(r.get(c, "") or "")[:widths[c]]).ljust(widths[c])
            for c in cols
        ))
    if len(rows) == limit:
        lines.append(f"\n({limit} rows — limit reached, raise --limit if needed)")
    else:
        lines.append(f"\n({len(rows)} rows)")
    return "\n".join(lines)


@mcp.tool()
def db_schema(table: str = "") -> str:
    """List Watchtower tables and their columns.

    Args:
      table: optional — if given, show just that table's columns
    """
    if table:
        rows = _query("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %(t)s
            ORDER BY ordinal_position
        """, {"t": table})
        if not rows:
            return f"No table named '{table}'."
        lines = [f"COLUMNS for {table}:", ""]
        for r in rows:
            null = "NULL" if r["is_nullable"] == "YES" else "NOT NULL"
            lines.append(f"  {r['column_name']:<28} {r['data_type']:<20} {null}")
        return "\n".join(lines)

    rows = _query("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    return "TABLES:\n" + "\n".join(f"  {r['table_name']}" for r in rows)


# ═════════════════════════════════════════════════════════════════════════════
# Auth middleware + app assembly
# ═════════════════════════════════════════════════════════════════════════════

PUBLIC_PATHS = {
    "/health",
    "/db-test",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/register",
    "/authorize",
    "/token",
}


class AuthMiddleware:
    """Pure ASGI middleware for Bearer token auth + rate limiting."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())[:8]
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
        host = headers.get(b"host", b"").decode("utf-8", errors="replace") or "localhost"
        scheme = headers.get(b"x-forwarded-proto", b"https").decode("utf-8", errors="replace")
        if PUBLIC_BASE_URL:
            resource_metadata_url = f"{PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"
        else:
            resource_metadata_url = f"{scheme}://{host}/.well-known/oauth-protected-resource"

        async def send_json(status: int, body: dict, extra_headers: dict = {}):
            body_bytes = json.dumps(body).encode()
            headers_list = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body_bytes)).encode()),
            ]
            for k, v in extra_headers.items():
                headers_list.append((k.encode(), v.encode()))
            await send({"type": "http.response.start", "status": status, "headers": headers_list})
            await send({"type": "http.response.body", "body": body_bytes})

        if not auth_header.startswith("Bearer "):
            www = f'Bearer resource_metadata="{resource_metadata_url}"'
            await send_json(401, {"error": "Unauthorized", "request_id": request_id},
                            {"WWW-Authenticate": www})
            return

        token = auth_header[7:]
        token_ok = (
            (MCP_AUTH_TOKEN and secrets.compare_digest(token, MCP_AUTH_TOKEN))
            or _is_valid_oauth_token(token)
        )
        if not token_ok:
            www = f'Bearer error="invalid_token", resource_metadata="{resource_metadata_url}"'
            await send_json(401, {"error": "Unauthorized", "request_id": request_id},
                            {"WWW-Authenticate": www})
            return

        allowed, retry_after = await global_limiter.acquire()
        if not allowed:
            await send_json(429, {"error": "Too Many Requests", "retry_after": retry_after},
                            {"Retry-After": str(retry_after)})
            return

        method = scope.get("method", "")
        logger.info(f"Request {method} {path}", extra={"request_id": request_id})
        await self.app(scope, receive, send)


def create_app():
    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request: Request) -> Response:
        return JSONResponse({
            "status": "ok",
            "service": "watchtower-mcp",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools": 15,
            "oauth": True,
            "db_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
            "transport": "postgrest",
        })

    @mcp.custom_route("/db-test", methods=["GET"])
    async def db_test_route(request: Request) -> Response:
        """Diagnostic — attempts a basic query, returns the actual error if any."""
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or not (
            MCP_AUTH_TOKEN and secrets.compare_digest(auth[7:], MCP_AUTH_TOKEN)
        ):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        import traceback
        try:
            rows = _query("SELECT 1 AS one, current_database() AS db, count(*) AS n_tickers FROM tickers")
            return JSONResponse({
                "ok": True,
                "rows": rows,
                "supabase_url": SUPABASE_URL,
            })
        except Exception as e:
            return JSONResponse({
                "ok": False,
                "error_type": type(e).__name__,
                "error": str(e)[:1000],
                "traceback": traceback.format_exc()[:2000],
                "supabase_url": SUPABASE_URL,
            }, status_code=500)

    @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
    async def oauth_protected_resource(request: Request) -> Response:
        iss = _issuer_for(request)
        return JSONResponse({
            "resource": iss,
            "authorization_servers": [iss],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp"],
        })

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
    async def oauth_authorization_server(request: Request) -> Response:
        iss = _issuer_for(request)
        return JSONResponse({
            "issuer": iss,
            "authorization_endpoint": f"{iss}/authorize",
            "token_endpoint": f"{iss}/token",
            "registration_endpoint": f"{iss}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256", "plain"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": ["mcp"],
        })

    @mcp.custom_route("/register", methods=["POST"])
    async def oauth_register(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        redirect_uris = body.get("redirect_uris") or []
        if not isinstance(redirect_uris, list) or not redirect_uris:
            return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
        client_id = secrets.token_urlsafe(16)
        async with _oauth_lock:
            _oauth_clients[client_id] = {
                "redirect_uris": [str(u) for u in redirect_uris],
                "client_name": body.get("client_name", "Claude"),
                "registered_at": time.time(),
            }
        logger.info(f"OAuth client registered: {client_id}")
        return JSONResponse({
            "client_id": client_id,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "client_id_issued_at": int(time.time()),
        }, status_code=201)

    @mcp.custom_route("/authorize", methods=["GET", "POST"])
    async def oauth_authorize(request: Request) -> Response:
        params = request.query_params if request.method == "GET" else await request.form()
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        response_type = params.get("response_type", "")
        state = params.get("state", "")
        code_challenge = params.get("code_challenge", "")
        code_challenge_method = params.get("code_challenge_method", "S256")
        scope = params.get("scope", "mcp")

        client = _oauth_clients.get(client_id)
        if not client:
            return JSONResponse({"error": "invalid_client"}, status_code=400)
        if redirect_uri not in client["redirect_uris"]:
            return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
        if response_type != "code":
            return _err_redirect(redirect_uri, "unsupported_response_type", state)
        if not code_challenge:
            return _err_redirect(redirect_uri, "invalid_request", state, "code_challenge required")

        if request.method == "GET":
            return HTMLResponse(_authorize_page(client_id, redirect_uri, state,
                                                code_challenge, code_challenge_method, scope,
                                                client.get("client_name", "Claude")))

        token_input = (params.get("auth_token") or "").strip()
        if not MCP_AUTH_TOKEN or not secrets.compare_digest(token_input, MCP_AUTH_TOKEN):
            return HTMLResponse(_authorize_page(client_id, redirect_uri, state,
                                                code_challenge, code_challenge_method, scope,
                                                client.get("client_name", "Claude"),
                                                error="Incorrect token. Try again."), status_code=401)

        code = secrets.token_urlsafe(32)
        async with _oauth_lock:
            _oauth_codes[code] = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "scope": scope,
                "expires_at": time.time() + AUTH_CODE_TTL,
            }
        logger.info(f"OAuth code issued for client {client_id}")
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}",
                                status_code=302)

    @mcp.custom_route("/token", methods=["POST"])
    async def oauth_token(request: Request) -> Response:
        try:
            form = await request.form()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        if form.get("grant_type", "") != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        code = form.get("code", "")
        client_id = form.get("client_id", "")
        redirect_uri = form.get("redirect_uri", "")
        code_verifier = form.get("code_verifier", "")

        async with _oauth_lock:
            _purge_expired_oauth()
            entry = _oauth_codes.pop(code, None)

        if not entry:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if entry["client_id"] != client_id or entry["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "client/redirect mismatch"}, status_code=400)
        if not _verify_pkce(code_verifier, entry["code_challenge"], entry["code_challenge_method"]):
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "PKCE verification failed"}, status_code=400)

        access_token = _derive_oauth_token(client_id)
        async with _oauth_lock:
            _oauth_tokens[access_token] = {
                "client_id": client_id,
                "scope": entry["scope"],
                "expires_at": time.time() + ACCESS_TOKEN_TTL,
            }
        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL,
            "scope": entry["scope"],
        })

    mcp_app = mcp.streamable_http_app()
    return AuthMiddleware(mcp_app)


app = create_app()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(f"Starting Watchtower MCP server on port {PORT}")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        log_level="warning",
        access_log=False,
    )
