#!/usr/bin/env python
"""
Watchtower MCP Server - Official MCP SDK version

This version uses the standard mcp Python SDK (FastMCP) for maximum compatibility
with Grok, Claude, and other MCP clients.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
import uvicorn

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", 8080))
# Railway sets RAILWAY_PUBLIC_DOMAIN to the service's public hostname.
# FastMCP's transport_security validates the Host header against this value.
PUBLIC_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "localhost")

# Error monitoring — inactive unless SENTRY_DSN is set in Railway variables.
# This codebase's failure mode is the silent kind (swallowed exceptions,
# hung threads found hours later); Sentry captures every unhandled error
# with a stack trace and emails on new issues.
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=_SENTRY_DSN, traces_sample_rate=0, send_default_pii=False)
    except Exception as _sentry_err:
        print(f"[server] Sentry init failed (monitoring disabled): {_sentry_err}")

mcp = FastMCP(
    "watchtower",
    streamable_http_path="/mcp",
    host=PUBLIC_DOMAIN,
)

PUBLIC_PATHS = {"/health"}
OAUTH_PATHS = {
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/authorize",
    "/token",
}

# In-memory store for one-time auth codes:
#   code -> (redirect_uri, expires_at, code_challenge, challenge_method)
_auth_codes: dict[str, tuple[str, float, str, str]] = {}

# Only redirect back to known MCP-client callback origins. Without this,
# /authorize + /token together let anyone who finds the hostname mint a
# working bearer token with two curl calls. Extend via OAUTH_REDIRECT_ALLOW
# (comma-separated URL prefixes) if a new client's callback isn't covered —
# the 400 response echoes the attempted URI to make that painless.
_REDIRECT_ALLOW = [p.strip() for p in os.environ.get(
    "OAUTH_REDIRECT_ALLOW",
    "https://claude.ai/,https://claude.com/,https://www.claude.com/,"
    "https://grok.com/,https://www.grok.com/,https://x.ai/,https://accounts.x.ai/",
).split(",") if p.strip()]


def _redirect_allowed(uri: str) -> bool:
    return any(uri.startswith(prefix) for prefix in _REDIRECT_ALLOW)


def _mcp_session_token() -> str:
    """Bearer token handed to OAuth clients — HMAC-derived from the master
    token so the master secret itself never transits the OAuth flow.
    Deterministic (no storage; survives restarts; all clients share it) and
    rotates automatically whenever MCP_AUTH_TOKEN is rotated."""
    if not MCP_AUTH_TOKEN:
        return ""
    return "wts_" + hmac.new(
        MCP_AUTH_TOKEN.encode(), b"watchtower-mcp-session-v1", hashlib.sha256
    ).hexdigest()


def _get_screens():
    """Lazy load the screen runners from the screen/ package."""
    from screen.reversal_screen import run_screen as run_reversal
    from screen.momentum_screen import run_screen as run_momentum
    from screen.breakdown_screen import run_screen as run_breakdown
    from screen.master_screen import run_screen as run_master
    from screen.insider_burst_screen import run_screen as run_insider
    from screen.upcomer_screen import run_screen as run_upcomer
    from screen.volume_burst_screen import run_screen as run_volume_burst
    return run_reversal, run_momentum, run_breakdown, run_master, run_insider, run_upcomer, run_volume_burst


# ============================================================
# Hidden-gem bottleneck engine — read the cache the nightly scan writes.
# (This is the same data that powers the dashboard Hidden Gems tab + daily
# email, so Grok, the UI, and the email all tell the SAME story.)
# ============================================================
_GEM_COLS = (
    "ticker, company_name, sector, industry, current_price, up_and_comer_score, "
    "signal, sleeve, theme, bottleneck, thesis, hot_sector, sector_heat, ret_6m_pct, "
    "vol_trend_d, vol_trend_w, buzz_7d, buzz_accel, buzz_x_level, buzz_x_rising, "
    "buzz_x_note, fund_score, rev_yoy_pct, piotroski, altman_z, gross_margin_pct, "
    "market_cap, market_regime, scored_date"
)


def _read_gem_cache(top_n: int = 10, ticker: str = "") -> list:
    """Top gems from the latest scored_date in up_and_comers_cache (or one ticker)."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            if ticker:
                cur.execute(
                    f"SELECT {_GEM_COLS} FROM up_and_comers_cache "
                    "WHERE ticker = %s AND scored_date = "
                    "(SELECT max(scored_date) FROM up_and_comers_cache)",
                    (ticker.upper(),),
                )
            else:
                cur.execute(
                    f"SELECT {_GEM_COLS} FROM up_and_comers_cache "
                    "WHERE scored_date = (SELECT max(scored_date) FROM up_and_comers_cache) "
                    "ORDER BY up_and_comer_score DESC NULLS LAST LIMIT %s",
                    (top_n,),
                )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _gem_exclusion_reason(ticker: str) -> str:
    """Why a specific ticker is NOT a hidden-gem candidate — computed from data so
    we state the ACTUAL disqualifier instead of a vague catch-all. Gates mirror
    analysis/hidden_gems.py (CAP 100M-10B, <100%/6mo, within -5%..+35% of 30w base)."""
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH px AS (
                    SELECT (array_agg(close ORDER BY trade_date DESC))[1] AS last_close,
                           (array_agg(close ORDER BY trade_date DESC)
                              FILTER (WHERE trade_date <= CURRENT_DATE - 182))[1] AS close_6m,
                           AVG(close) FILTER (WHERE trade_date >= CURRENT_DATE - 210) AS sma_30w
                    FROM daily_prices WHERE ticker = %s AND trade_date >= CURRENT_DATE - 250)
                SELECT t.market_cap, t.industry, p.last_close,
                       (p.last_close / NULLIF(p.close_6m, 0) - 1) AS ret6,
                       (p.last_close / NULLIF(p.sma_30w, 0) - 1) AS vs_sma
                FROM tickers t LEFT JOIN px p ON true WHERE t.ticker = %s
                """,
                (ticker, ticker),
            )
            row = cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not row:
        return "no ticker/price data on file for it"
    mcap, ind, last, ret6, vs = row
    mcap = float(mcap) if mcap is not None else None  # Decimal from Postgres → float for the /1e9 math below
    reasons = []
    if mcap is None:
        reasons.append("no market cap on file")
    elif mcap < 100e6:
        reasons.append(f"market cap ${mcap/1e6:.0f}M is below the $100M floor")
    elif mcap > 10e9:
        reasons.append(f"market cap ${mcap/1e9:.1f}B is above the $10B gem ceiling")
    if ret6 is not None and float(ret6) >= 1.0:
        reasons.append(f"already up {float(ret6)*100:.0f}% over 6 months (past the not-parabolic limit)")
    if vs is not None:
        v = float(vs)
        if v >= 0.35:
            reasons.append(f"stretched {v*100:.0f}% above its 30-week base (extended)")
        elif v < -0.05:
            reasons.append(f"{abs(v)*100:.0f}% below its 30-week base (downtrend, not a coiled setup)")
    if not reasons:
        reasons.append("it clears the size/price gates, but its industry isn't currently flagged as a "
                       "hot-sector bottleneck (or it didn't rank in today's top 50)")
    return "; ".join(reasons)


def _fmt_gem(r: dict) -> str:
    mcap = float(r.get("market_cap") or 0)  # Postgres numeric arrives as Decimal; float() avoids Decimal/float errors
    mcap_s = (f"${mcap/1e9:.1f}B" if mcap >= 1e9 else f"${mcap/1e6:.0f}M") if mcap else "—"
    vw = r.get("vol_trend_w")
    vol_s = f"vol {float(vw):.1f}x/wk" if vw is not None else ""
    xl = r.get("buzz_x_level")
    x_s = f" | 𝕏 {xl}{' ▲' if r.get('buzz_x_rising') else ''}" if xl and xl != "none" else ""
    score = float(r.get("up_and_comer_score") or 0)
    sleeve = (r.get("sleeve") or r.get("signal") or "").upper()
    # fundamentals snippet
    fb = []
    if r.get("rev_yoy_pct") is not None:
        fb.append(f"rev {float(r['rev_yoy_pct']):+.0f}% YoY")
    if r.get("gross_margin_pct") is not None:
        fb.append(f"{float(r['gross_margin_pct']):.0f}% GM")
    if r.get("piotroski") is not None:
        fb.append(f"Piotroski {int(r['piotroski'])}/9")
    fund_s = (" | " + ", ".join(fb)) if fb else ""
    head = (f"- **{r['ticker']}** ({(r.get('company_name') or '')[:26]}, {mcap_s}) | "
            f"Score {score:.0f} | {sleeve} | {r.get('theme','')} → {r.get('bottleneck','')} "
            f"| {vol_s}{fund_s}{x_s}")
    thesis = r.get("thesis")
    return head + (f"\n  {thesis}" if thesis else "")



@mcp.tool()
def watchtower_run_screen(
    screen: str,
    top_n: int = 5,
    with_plan: bool = True,
    with_synthesis: bool = False,
    ticker: str = "",
) -> str:
    """Run one of Watchtower's stock screens live.

    Supports:
    - reversal: beaten-down quality stocks turning up (8/13 EMA, RSI recovery, etc.)
    - momentum: strong getting stronger — near 52w highs, established trends
    - breakdown: bearish ideas, shorting candidates
    - master: broad fundamental composite
    - insider: insider activity driven
    - upcomer / gems / hidden_gems: Hidden Gems — small/mid-caps fixing the supply-chain
      bottleneck of the market's hottest sectors, set up to move next, not yet parabolic
      (from the nightly bottleneck scan; same as watchtower_get_hidden_gems)
    - volume_burst: unusual volume surges — breakouts and exhaustion signals

    Use ticker="AAPL" to score any single stock through the chosen screen, regardless of
    whether it's in the quality universe. Great for on-demand stock lookups.
    Use with_plan=true to include suggested trade plan (ATR stop + position size).
    Use with_synthesis=true to append a Grok AI narrative synthesizing the top results (requires XAI_API_KEY).
    """
    run_reversal, run_momentum, run_breakdown, run_master, run_insider, run_upcomer, run_volume_burst = _get_screens()
    t = ticker.upper() if ticker else None

    if screen == "reversal":
        results = run_reversal(min_drawdown=15.0, single_ticker=t)[:top_n]
    elif screen == "momentum":
        results = run_momentum(max_pullback=12.0, single_ticker=t)[:top_n]
    elif screen == "breakdown":
        results = run_breakdown(min_breakdown=45.0, single_ticker=t)[:top_n]
    elif screen == "master":
        results = run_master(single_ticker=t)[:top_n]
    elif screen == "insider":
        results = run_insider(single_ticker=t)[:top_n]
    elif screen in ("upcomer", "hidden_gems", "gems"):
        # Hidden Gems come from the nightly bottleneck engine's cache, not a live
        # per-ticker screen — hand off to the dedicated formatter.
        return watchtower_get_hidden_gems(top_n=top_n)
    elif screen == "volume_burst":
        results = run_volume_burst(min_surge=1.75, single_ticker=t)[:top_n]
    else:
        return f"Unknown screen '{screen}'. Valid options: reversal, momentum, breakdown, master, insider, upcomer, volume_burst"

    lines = [f"**{screen.upper()} SCREEN RESULTS** (Top {len(results)})"]
    for r in results:
        score = (
            r.get("reversal_score")
            or r.get("momentum_score")
            or r.get("breakdown_score")
            or r.get("score", "N/A")
        )
        line = f"- **{r.get('ticker')}** | {(r.get('company_name') or '')[:28]} | Score: {score}"
        if with_plan and r.get("plan"):
            p = r["plan"]
            # `or 0` (not a .get default) so a present-but-None value can't reach
            # the :.2f format — that raises "unsupported format string passed to
            # NoneType.__format__" and breaks the whole tool call (Sentry ToolError).
            line += f" | Stop: ${(p.get('stop_price') or 0):.2f} | Size: {(p.get('position_pct') or 0):.1f}%"
        lines.append(line)

    if with_synthesis:
        try:
            from analysis.grok_synthesizer import synthesize_screen_results
            narrative = synthesize_screen_results(screen, results, top_n=min(top_n, len(results)))
            if narrative:
                lines.append(f"\n**AI Analysis:**\n{narrative}")
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
def watchtower_intraday_scan(top_n: int = 10, ticker: str = "", with_synthesis: bool = False) -> str:
    """Scan for intraday setups forming right now using live Polygon data (15-min delayed on Starter tier).

    Bullish: GAP_AND_GO, INTRADAY_BREAKOUT, VWAP_BREAKOUT, FLUSH_REVERSAL, GAP_REVERSAL
    Bearish: VWAP_REJECTION, INTRADAY_BREAKDOWN, GAP_DOWN_CONFIRM, DISTRIBUTION
    Neutral: VOLUME_SURGE (unusual activity, direction unclear)

    Use ticker="ONDS" to check a specific stock intraday.
    Use with_synthesis=true for Grok AI narrative on the top setups.
    Best used during market hours (9:30 AM - 4:00 PM ET).
    """
    from screen.intraday_screen import run_screen as run_intraday
    t = ticker.upper() if ticker else None
    results = run_intraday(single_ticker=t)[:top_n]

    if not results:
        return "No intraday setups detected above threshold right now."
    if results and results[0].get("error"):
        return f"Intraday scan error: {results[0]['error']}"

    # Check market hours from first result
    is_market_hours = results[0].get("is_market_hours", True) if results else True
    minutes_elapsed = results[0].get("minutes_elapsed", 0) if results else 0
    header = "**INTRADAY SCAN** (Live Polygon — 15-min delayed)"
    if not is_market_hours:
        header += " ⚠️ Market closed — showing last session data"
    else:
        header += f" | {minutes_elapsed}min into session"

    lines = [header]
    for r in results:
        # `or 0`/`or ''` (not .get defaults) so a present-but-None value can't reach
        # a format spec — :.0f/:<18 on None raises "unsupported format string passed
        # to NoneType.__format__" and breaks the whole tool call.
        line = (f"- **{r.get('ticker')}** | {(r.get('signal_type') or ''):<18} | Score: {(r.get('score') or 0):.0f}"
                f" | {(r.get('change_pct') or 0):+.1f}% | Vol: {(r.get('vol_pace_ratio') or 0):.1f}x"
                f" | {'↑VWAP' if r.get('above_vwap') else '↓VWAP'}"
                f" | ${(r.get('current_price') or 0):.2f}"
                f"  {r.get('rationale','')}")
        lines.append(line)

    if with_synthesis:
        try:
            from analysis.grok_synthesizer import synthesize_screen_results
            narrative = synthesize_screen_results("intraday", results, top_n=min(top_n, len(results)))
            if narrative:
                lines.append(f"\n**AI Analysis:**\n{narrative}")
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
def watchtower_get_momentum(top_n: int = 5) -> str:
    """Get current top momentum / up-and-comers from Watchtower's momentum sleeve."""
    _, run_momentum, *_ = _get_screens()  # noqa: F841
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
    The Hidden Gems / Next-Parabolic watchlist — the SAME data shown on the
    dashboard Hidden Gems tab and the daily email (reads the nightly bottleneck
    scan's cache; does not recompute live).

    How the list is built (supply-chain bottleneck engine):
    - Rank the market's hottest sectors/industries (price momentum + analyst
      revisions + news + social).
    - For each hot theme, identify the supply-chain BOTTLENECK that must scale
      for it to keep running (e.g. AI compute → memory/HBM like MU, grid power,
      datacenter build-out, cooling, components).
    - Surface $100M–$10B names in those bottleneck industries that have NOT yet
      gone parabolic (excluded if up >100% in 6mo or stretched >35% above their
      30-week base) and are just turning up — favoring rising volume and building
      X chatter.

    Each gem includes its theme → bottleneck, score, signal, 6-month return,
    daily/weekly volume trend, the Grok-X buzz read, and a one-line thesis.
    """
    gems = _read_gem_cache(top_n)
    if not gems:
        return ("No hidden gems in the latest scan. The bottleneck scan runs pre-market "
                "(6 AM ET, Mon–Fri) and writes the day's list to the cache.")
    as_of = gems[0].get("scored_date")
    regime = next((g.get("market_regime") for g in gems if g.get("market_regime")), None)
    _RT = {
        "risk_on":  "RISK-ON (breadth healthy — reversals given more room, momentum trusted normally)",
        "neutral":  "NEUTRAL (mixed breadth — balanced sleeves)",
        "risk_off": "RISK-OFF (breadth weak — momentum de-emphasized, fundamentals lead, list tightened)",
    }
    regime_line = f"\n*Market regime: {_RT.get(regime, regime.upper())}*" if regime else ""
    lines = [
        f"**HIDDEN GEMS — Next-Parabolic Watch** (Top {len(gems)}, scanned {as_of})",
        "*Two evidence-backed sleeves in the market's hot bottleneck industries: "
        "MOMENTUM (strong & still trending) and REVERSAL (beaten down, turning up). "
        "Rising volume + real fundamentals (quality/growth) favored; distress/dilution/"
        "negative-margin junk and true blow-offs are screened out. Scoring is regime-aware — "
        "momentum is trusted less when market breadth is weak.*"
        + regime_line + "\n",
    ]
    for r in gems:
        lines.append(_fmt_gem(r))
    return "\n".join(lines)


@mcp.tool()
def watchtower_fair_value(ticker: str, discount_rate: float = 10.0,
                          growth_rate: float = 0.0, years: int = 10) -> str:
    """
    Estimate a stock's intrinsic fair value and upside/downside vs. its price.

    A simple 2-stage discounted-cash-flow model on trailing free cash flow (the
    shape of Qualtrim's stock-price estimator): grow FCF for `years`, decaying
    toward a long-run rate, discount back, add a Gordon terminal value, divide by
    shares. Falls back to an EPS × exit-multiple estimate for names with negative
    FCF but positive earnings. Also surfaces fundamental red flags (negative FCF,
    distress Altman Z, weak Piotroski, dilution, leverage, revenue/margin erosion).

    A conservative value anchor to pair with the momentum/rotation signals — not a
    price target.

    Args:
      discount_rate: required annual return %, default 10. Lower it to see what
        growth/return the current price is implying.
      growth_rate: near-term FCF/EPS growth %. 0 (default) = derive it from the
        analyst EPS estimate ladder automatically.
      years: projection horizon, default 10.
    """
    ticker = ticker.upper().strip()
    try:
        from analysis.fundamental_value import compute_fair_value, fundamentals_snapshot
    except Exception as e:
        return f"Fair-value module unavailable: {e}"
    fv = compute_fair_value(
        ticker,
        discount_rate=discount_rate / 100.0,
        growth_rate=(growth_rate / 100.0) if growth_rate else None,
        years=years,
    )
    snap = fundamentals_snapshot(ticker)
    flags = snap.get("red_flags") or []
    flag_line = ("⚠ Red flags: " + "; ".join(flags)) if flags else "✓ No major fundamental red flags."
    if not fv or fv.get("fair_value") is None:
        why = (fv or {}).get("note") or ("needs positive trailing FCF or earnings "
                                         "(likely unprofitable on both)")
        return f"**{ticker} — no fair-value estimate.** {why}\n{flag_line}"
    a = fv["assumptions"]
    up = fv.get("upside_pct")
    up_s = (f"{up*100:+.0f}% {'upside' if up >= 0 else 'downside'}") if up is not None else "n/a"
    lines = [
        f"**{ticker} — FAIR VALUE ${fv['fair_value']:,.2f}** vs price "
        f"${fv['price']:,.2f} → **{up_s}**",
    ]
    if fv.get("confidence") == "low":
        lines.append(f"⚠ LOW CONFIDENCE — {fv.get('note') or 'inputs are weak for this name'}")
    lines.append(
        f"*{fv['method']} · {a['growth_rate']*100:.1f}% growth "
        f"({a['growth_source']}) → {a['terminal_growth']*100:.1f}% terminal · "
        f"{a['discount_rate']*100:.0f}% discount · {a['years']}y*")
    lines.append(flag_line)
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
    (run_reversal, run_momentum, run_breakdown, run_master,
     run_insider, run_upcomer, run_volume_burst) = _get_screens()

    sleeve_results = []

    # Run every screen with the single ticker
    screen_configs = [
        ("REVERSAL",     lambda: run_reversal(min_drawdown=5.0, single_ticker=ticker)),
        ("MOMENTUM",     lambda: run_momentum(max_pullback=20.0, single_ticker=ticker)),
        ("BREAKDOWN",    lambda: run_breakdown(min_breakdown=20.0, single_ticker=ticker)),
        ("MASTER",       lambda: run_master(single_ticker=ticker)),
        ("INSIDER",      lambda: run_insider(single_ticker=ticker)),
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

    # Upcomer / Hidden Gem — from the new bottleneck engine's cache (the same
    # list the dashboard + email use), NOT a live per-ticker technical screen.
    try:
        hits = _read_gem_cache(top_n=1, ticker=ticker)
        if hits:
            g = hits[0]
            xl = g.get("buzz_x_level")
            x_s = f"; 𝕏 chatter {xl}{' rising' if g.get('buzz_x_rising') else ''}" if xl and xl != "none" else ""
            rationale = (f"HIDDEN GEM ({g.get('signal','')}) — {g.get('theme','')} → "
                         f"{g.get('bottleneck','')}. {g.get('thesis','') or ''}{x_s}")
            sleeve_results.append({
                "sleeve": "UPCOMER",
                "score": float(g.get("up_and_comer_score") or 0),
                "data": {"ticker": ticker, "score": float(g.get("up_and_comer_score") or 0),
                         "rationale": rationale},
            })
        else:
            why = _gem_exclusion_reason(ticker)
            sleeve_results.append({
                "sleeve": "UPCOMER",
                "score": 0,
                "data": {"ticker": ticker, "score": 0,
                         "rationale": f"Not a current hidden-gem candidate — {why}."},
            })
    except Exception as e:
        sleeve_results.append({"sleeve": "UPCOMER", "score": 0, "error": str(e)[:80]})

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
            # `or 0`, not a .get default — a present-but-None value would hit
            # the :+.1f format and crash the whole tool call (same class as
            # the fixed Sentry ToolErrors).
            change = data.get("change_pct") or 0
            vol_pace = data.get("vol_pace_ratio") or 0
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
            surge = data.get("surge_ratio") or 0
            line += f" | Signal: {signal} | Surge: {surge:.1f}x"
        else:
            rationale = data.get("rationale", "") or data.get("plan_rationale", "")
            if rationale:
                line += f"\n  → {rationale}"

        lines.append(line)

    if errored:
        lines.append("")
        lines.append("*Sleeves with errors: " + ", ".join(s["sleeve"] for s in errored) + "*")

    # Watchtower Oscillator — one-line daily read (confirmed bars, no repaint)
    try:
        from analysis.oscillator import compute_for_ticker, describe_read
        osc = compute_for_ticker(ticker, "daily")
        if osc:
            lines.append("")
            lines.append(f"**OSCILLATOR (daily)** | {describe_read(osc)} — "
                         "full multi-timeframe read: watchtower_get_oscillator")
    except Exception:
        pass

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
                    f"(score {(buzz.get('sentiment_score') or 0):+.2f}, "
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


@mcp.tool()
def watchtower_get_patterns(timeframe: str = "all", status: str = "all",
                            direction: str = "all", top_n: int = 30,
                            pattern: str = "ALL") -> str:
    """
    Live classical chart-pattern detections across weekly, daily, and 4h bars.

    Patterns: Inverse H&S (low → equal-or-lower low → HIGHER low → neckline),
    H&S top, double bottom/top, bull/bear flag, ascending/descending triangle,
    falling/rising wedge, cup & handle, long-term range breakout/breakdown. The scan keeps only LIVE setups — 'forming' (price
    hasn't crossed the trigger line yet: the entry window) and fresh
    'breakout' (crossed within the last few bars, not extended). Weekly and
    daily detect closing-price structure; 4h uses true highs/lows.

    Args:
        timeframe: all | weekly | daily | 4h
        status:    all | forming | breakout
        direction: all | bullish | bearish
        top_n:     max rows (default 30)
        pattern:   ALL or one pattern key (e.g. ema_bounce, inverse_hs,
                   double_bottom) — filtered in SQL, so every setup of that
                   type is reachable regardless of overall score rank
    """
    try:
        from dashboard.api import _pattern_rows
        from analysis.pattern_scan import PATTERN_NAMES
        data = _pattern_rows(timeframe.lower(), status.lower(), direction.lower(),
                             pattern)
        rows = data.get("rows") or []
        if not rows:
            return ("No live patterns match. The scan runs daily at 6:45 AM ET "
                    "(4h refresh 12:45 PM); try watchtower_scan_patterns to rescan now.")
        lines = [f"**Live chart patterns** ({len(rows)} total, showing {min(top_n, len(rows))}) "
                 f"— as of {data.get('as_of') or 'n/a'}", ""]
        for r in rows[:top_n]:
            name = PATTERN_NAMES.get(r["pattern"], r["pattern"])
            arrow = "▲" if r["direction"] == "bullish" else "▼"
            rs = f" · RS {r['rs_pct']}" if r.get("rs_pct") is not None else ""
            sec = f" · {r['sector']}" if r.get("sector") else ""
            dist = r.get("dist_pct")
            dist_s = f"{dist:+.1f}% vs trigger" if dist is not None else ""
            est = r.get("est") or {}
            est_s = ""
            if est.get("dte"):
                tag = "measured" if est.get("source") == "measured" else "est"
                est_s = (f" · resolves ~{est['weeks_lo']}-{est['weeks_hi']}w "
                         f"({tag}) → ≥{est['dte']} DTE")
            lines.append(
                f"- **{r['ticker']}** {arrow} {name} ({r['timeframe']}, {r['status']}) — "
                f"px ${r['last_close']:,.2f}, trigger ${r['trigger']:,.2f} ({dist_s}), "
                f"target ${r['target']:,.2f}, invalid ${r['invalid']:,.2f} · "
                f"score {r['score']:.0f}{rs}{sec}{est_s}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching patterns: {e}"


@mcp.tool()
def watchtower_scan_patterns(include_4h: bool = True) -> str:
    """
    Re-run the chart-pattern scan right now (weekly + daily from the DB,
    optionally the 4h Polygon pass). Runs in the background — results land in
    watchtower_get_patterns / the dashboard Patterns tab in a few minutes.
    """
    try:
        import threading
        from analysis.pattern_scan import run_pattern_scan

        def _run():
            try:
                run_pattern_scan(include_4h=include_4h)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"pattern rescan failed: {e}")

        threading.Thread(target=_run, name="pattern-rescan", daemon=True).start()
        return ("Pattern scan started (weekly + daily"
                + (" + 4h" if include_4h else "")
                + "). Check watchtower_get_patterns in ~2-5 minutes.")
    except Exception as e:
        return f"Error starting pattern scan: {e}"


@mcp.tool()
def watchtower_get_oscillator(ticker: str, timeframe: str = "all") -> str:
    """
    Watchtower Oscillator read for one ticker — WaveTrend wave pair, money
    flow, RSI/StochRSI, Williams %R(28)+EMA, MACD, fired signals, and a
    0-100 confluence score per timeframe. Computed fresh on CONFIRMED bars
    only (the live bar is never used, so nothing here can repaint).

    Args:
        ticker: symbol
        timeframe: all | monthly | weekly | daily | 4h | 1h | 2d | 3d | 5m
                   ('all' = weekly + daily + 4h + 1h; the others on request.
                   monthly fetches ~7 years of history from Polygon and only
                   uses CLOSED months — mid-month monthly signals are
                   provisional until month-end, so they never appear early)
    """
    try:
        from analysis.oscillator import (compute_for_ticker, describe_read,
                                         _fmt_bar_ts, ON_DEMAND_TFS)
        ticker = ticker.upper().strip()
        tf = timeframe.lower().strip()
        tfs = ("weekly", "daily", "4h", "1h") if tf == "all" else (tf,)
        if any(t not in ON_DEMAND_TFS for t in tfs):
            return f"Unknown timeframe '{timeframe}'. Use all | {' | '.join(ON_DEMAND_TFS)}."
        lines = [f"**Watchtower Oscillator — ${ticker}** "
                 "(confirmed bars only — signals cannot repaint)", ""]
        got = 0
        for t in tfs:
            r = compute_for_ticker(ticker, t)
            if not r:
                lines.append(f"- **{t}**: not enough history (need 70+ confirmed bars)")
                continue
            got += 1
            lines.append(f"- **{t}** (bar {_fmt_bar_ts(r['bar_ts'], t)}, "
                         f"${r['close']:,.2f}): {describe_read(r)}")
            pc = r.get("pattern_ctx")
            if pc:
                lines.append(f"    pattern context: {pc['direction']} {pc['status']}"
                             + (f", trigger ${pc['trigger']:,.2f}" if pc.get("trigger") else ""))
        if not got:
            return f"${ticker}: no timeframe had enough history for an oscillator read."
        lines.append("")
        lines.append("Reading guide: wave beyond ±53 = actionable extreme (±60 = blown "
                     "out); 'flow washed out' + wave at the lower band + %R pinned = "
                     "fuel for a bullish turn; MACD is the confirmation layer.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error computing oscillator for {ticker}: {e}"


@mcp.tool()
def watchtower_screen_oscillator(setup: str = "entry_grade",
                                 timeframe: str = "daily",
                                 direction: str = "bullish",
                                 top_n: int = 15) -> str:
    """
    Pull names whose Watchtower Oscillator is flashing a setup. Reads the
    fleet scan (refreshed after each pattern scan: full at 6:45 AM ET,
    4h/1h again at 12:45 PM ET).

    entry_grade (default) = ENTRIES: supportive oscillator PLUS a live
    pattern in the trade direction near its trigger, a weekly that isn't
    fighting the trade, and a relative-strength floor. high_confluence =
    the raw washout watchlist (most stretched names in the market — where
    turns START; stalk for the higher low, don't buy the first green dot).

    Args:
        setup: entry_grade | high_confluence | coil | wt_extreme_cross |
               pctr_hook | divergence (price vs wave, confirmed pivots) |
               mf_round (smooth rounded money-flow turn — the arc, not a
               one-bar curl) | mf_curl | any_signal
        timeframe: daily | weekly | 4h | 1h
        direction: bullish | bearish | all   (default bullish)
        top_n: max rows (default 15)
    """
    try:
        from dashboard.api import _oscillator_rows
        setup = setup.lower().strip()
        data = _oscillator_rows(timeframe.lower().strip(),
                                direction.lower().strip(), setup)
        rows = (data.get("rows") or [])[:max(1, min(top_n, 50))]
        if not rows:
            return (f"No {timeframe} names currently match setup '{setup}' "
                    f"({direction}). The scan refreshes after each pattern scan "
                    "(6:45 AM / 12:45 PM ET).")
        lines = [f"**Oscillator screen — {setup}, {timeframe}, {direction}** "
                 f"({len(rows)} shown; scanned {data.get('as_of_et') or 'n/a'})", ""]
        def _n(v, spec):
            return format(v, spec) if v is not None else "n/a"
        for r in rows:
            arrow = "▲" if r["direction"] == "bullish" else "▼"
            sigs = ", ".join(r.get("signal_names") or []) or "—"
            sec = f" · {r['sector']}" if r.get("sector") else ""
            rs = f" · RS {r['rs_pct']}" if r.get("rs_pct") is not None else ""
            px = f"px ${r['close']:,.2f}, " if r.get("close") is not None else ""
            wk = f" · wkly {r['weekly_dir']}" if r.get("weekly_dir") else ""
            pat = ""
            if r.get("pattern"):
                d = r.get("pattern_dist")
                pat = (f" · {r['pattern']} ({r.get('pattern_tf','')} "
                       f"{r.get('pattern_status','')}"
                       + (f", {d:+.1f}% vs trigger" if d is not None else "") + ")")
            lines.append(
                f"- **{r['ticker']}** {arrow} {_n(r['confluence_score'], '.0f')}/100 — "
                f"{px}wt2 {_n(r['wt2'], '+.0f')}, "
                f"MF {_n(r['mf'], '+.1f')}, %R {_n(r['pctr'], '.0f')}, "
                f"MACDh {_n(r['macd_hist'], '+.2f')}{wk}{pat} · {sigs}{rs}{sec}")
        lines.append("")
        lines.append("Deep dive any name with watchtower_get_oscillator(ticker).")
        return "\n".join(lines)
    except Exception as e:
        return f"Error screening oscillator: {e}"


@mcp.tool()
def watchtower_option_ticket(ticker: str) -> str:
    """
    Turn a ticker's best live pattern into a concrete options ticket:
    expiry from the pattern's MEASURED resolution time (Est DTE), a
    ~0.65-delta directional leg, a vertical spread built from the
    pattern's own entry/target strikes, IV rich/cheap context (ATM IV vs
    21-day realized), open-interest liquidity gating, and an
    earnings-inside-the-window flag. Prices are 15-min delayed snapshot
    data — decision support; the broker's screen is the final price check.
    """
    try:
        from analysis.options_picker import build_ticket
        t = build_ticket(ticker)
        if not t:
            return (f"${ticker.upper()}: no live pattern on the board — "
                    "the ticket builder keys off pattern_scan. Check "
                    "watchtower_get_patterns or the Patterns tab.")
        if t.get("error"):
            return f"${t.get('ticker', ticker.upper())}: {t['error']} — {t.get('note','')}"
        arrow = "▲" if t["direction"] == "bullish" else "▼"
        lines = [f"**Option ticket — ${t['ticker']}** {arrow} "
                 f"{t['pattern']} ({t['timeframe']}, {t['status']}, score {t['score']:.0f})", ""]
        stop_s = f"${t['stop']:,.2f}" if t.get("stop") is not None else "n/a"
        lines.append(f"- Underlying plan: entry ${t['entry']:,.2f} · stop {stop_s} · "
                     f"target ${t['target']:,.2f} · last ${t['last_close']:,.2f}")
        wl, wh = t.get("est_weeks") or (None, None)
        est_s = f"~{wl}-{wh} weeks" if wl else "n/a"
        lines.append(f"- Resolution estimate: {est_s} → DTE floor {t['dte_floor']} → "
                     f"**expiry {t['expiry']}** (also available: "
                     f"{', '.join(t.get('expiries_available', [])[1:]) or '—'})")
        d = t["directional"]
        d_delta = f"{d['delta']:+.2f}Δ" if d.get("delta") is not None else "Δ n/a"
        d_iv = f", IV {d['iv']:.0%}" if d.get("iv") else ""
        lines.append(f"- Directional: **{d['exp']} ${d['strike']:g} "
                     f"{'call' if t['direction']=='bullish' else 'put'}** "
                     f"({d_delta}{d_iv}, OI {d.get('oi') or '?'}, "
                     f"last ${d.get('last') or '?'})")
        v = t.get("vertical")
        if v:
            deb = f"~${v['est_debit']}" if v.get("est_debit") else "n/a"
            lines.append(f"- Vertical (entry→target): long ${v['long']['strike']:g} / "
                         f"short ${v['short']['strike']:g} ({v['long']['exp']}), "
                         f"width ${v['width']:g}, est. debit {deb} → "
                         f"max value ${v['max_value']:g}")
        iv = t.get("iv")
        if iv:
            lines.append(f"- IV context: ATM {iv['atm_iv']:.0%} vs realized "
                         f"{iv['realized_21d']:.0%} (ratio {iv['ratio']}) — **{iv['read']}**")
        e = t.get("earnings")
        if e:
            lines.append(f"- ⚠️ **Earnings {e['date']} {e['when']}** — inside this "
                         "option's life. Decide now: ride through or exit before.")
        if t.get("oi_note"):
            lines.append(f"- ⚠️ {t['oi_note']}")
        lines.append("")
        lines.append("Prices are 15-min delayed last-trade/close — confirm live "
                     "pricing at your broker before entering.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error building option ticket for {ticker}: {e}"


@mcp.tool()
def watchtower_pattern_timing(rerun: bool = False) -> str:
    """
    How long each chart pattern takes to play out, measured from the
    historical replay (same detectors as the live scanner, no lookahead):
    per pattern — sample size, target-vs-invalidation hit rate, and the
    25th/50th/75th-percentile BARS from breakout to target touch. This is
    the empirical basis for picking option expiries: buy at least 2x the
    75th-percentile time.

    Args:
        rerun: re-run the replay in the background (~10 min) before reading.
    """
    try:
        from analysis.pattern_backtest import timing_stats, run_pattern_backtest
        from analysis.pattern_scan import PATTERN_NAMES
        if rerun:
            import threading
            threading.Thread(target=run_pattern_backtest,
                             name="pattern-backtest", daemon=True).start()
            return ("Pattern timing replay started in the background (~10 "
                    "minutes). Call again with rerun=False to read results.")
        stats = timing_stats()
        if not stats:
            return ("No pattern timing results yet — the replay seeds "
                    "automatically a few minutes after deploy, or pass "
                    "rerun=True.")
        lines = ["**Pattern time-to-target** (daily bars from breakout to "
                 "measured-move touch; winners only for the time columns)", "",
                 "| Pattern | N | Target hit % | p25 | Median | p75 | "
                 "Suggested min DTE |", "|---|---|---|---|---|---|---|"]
        for p, s in sorted(stats.items(), key=lambda kv: -(kv[1]["n"] or 0)):
            name = PATTERN_NAMES.get(p, p)
            if s.get("med") is None:
                lines.append(f"| {name} | {s['n']} | "
                             f"{s['hit_rate'] if s['hit_rate'] is not None else 'n/a'}% "
                             f"| n/a | n/a | n/a | n/a |")
                continue
            dte = int(round(s["p75"] / 5 * 7 * 2))
            lines.append(
                f"| {name} | {s['n']:,} | {s['hit_rate']:.1f}% | "
                f"{s['p25']:.0f} | {s['med']:.0f} | {s['p75']:.0f} | "
                f"~{dte}d |")
        lines.append("")
        lines.append("Bars are daily sessions (÷5 ≈ weeks). Weekly patterns "
                     "take a similar BAR count — read the median as weeks; "
                     "4h patterns as ~12 bars/week. The Patterns tab's "
                     "Est (DTE) column applies this automatically.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading pattern timing: {e}"


@mcp.tool()
def watchtower_oscillator_backtest(rerun: bool = False) -> str:
    """
    Historical backtest of the oscillator entry signals: every
    wt_extreme_cross and pctr_hook the scanner WOULD have fired on each
    historical bar (confirmed bars, no repaint — the replay is honest),
    with 21/63-session forward returns, split by whether the entry-grade
    quality gates (weekly wave agreeing + relative-strength floor) passed.
    Use this to judge which setups actually pay and how much the gates add.

    Args:
        rerun: re-run the replay in the background first (a few minutes)
               before reading; default False = read stored results.
    """
    try:
        from analysis.oscillator_backtest import backtest_report, run_backtest
        if rerun:
            import threading
            threading.Thread(target=run_backtest, name="osc-backtest",
                             daemon=True).start()
            return ("Backtest replay started in the background — takes a few "
                    "minutes over the full universe. Call this tool again "
                    "(rerun=False) shortly to read the refreshed results.")
        rep = backtest_report()
        if not rep["total_events"]:
            return ("No backtest results stored yet — it seeds automatically "
                    "a few minutes after deploy, or pass rerun=True.")
        lines = [f"**Oscillator backtest** — {rep['total_events']:,} events, "
                 f"{rep['window'][0]} → {rep['window'][1]} "
                 "(returns measured in the trade's direction)", ""]
        lines.append("| Signal | Dir | Gates | N | Win% (21d) | Avg 21d | "
                     "Med 21d | Excess vs SPY | Avg 63d |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in rep["rows"]:
            def p(v):
                return f"{v:+.2f}%" if v is not None else "n/a"
            lines.append(
                f"| {r['signal_type']} | {r['direction']} | "
                f"{'✅ gated' if r['gated'] else 'raw'} | {r['n']:,} | "
                f"{r['win_rate_21d_pct']:.1f}% | {p(r['avg_fwd21_pct'])} | "
                f"{p(r['med_fwd21_pct'])} | {p(r['avg_excess21_vs_spy_pct'])} | "
                f"{p(r['avg_fwd63_pct'])} |")
        lines.append("")
        lines.append("Gates = weekly wave rising with the trade + RS floor "
                     "(≥25 bullish / ≤75 bearish). The live entry-grade view "
                     "additionally requires a chart pattern near its trigger.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading oscillator backtest: {e}"


# ── OAuth 2.0 / PKCE endpoints ────────────────────────────────────────────────

@mcp.tool()
def watchtower_force_scan() -> str:
    """
    Manually trigger the intraday scan + news scan right now.
    Same as the scheduled job — runs the full pipeline and sends the email if signals are found.
    Useful for testing or forcing a scan outside the normal schedule.
    """
    try:
        from alerts.scheduler import run_scheduled_scan
        run_scheduled_scan()
        return "Scan triggered. Check your email — if signals were found above threshold, the alert was sent."
    except Exception as e:
        return f"Error triggering scan: {e}"


@mcp.tool()
def watchtower_alert_performance(
    days_back: int = 90,
    alert_type: str = None,
    export_csv: bool = False,
) -> str:
    """
    Show how past Watchtower signals have performed across all screens.

    Tracks every signal logged — intraday, gems, news, reversal, momentum,
    breakdown, and insider — with daily return fills at d1/d3/d7/d14/d30/d60/d90
    depending on the signal type's tracking window. Also shows peak return
    and which day it peaked, so you can see the full price path, not just the endpoint.

    Args:
        days_back:  How far back to look (default 90 days).
        alert_type: Filter to one type: intraday | gem | news | reversal |
                    momentum | breakdown | insider | master
        export_csv: If True, returns raw CSV instead of summary.
    """
    try:
        from analysis.alert_tracker import get_performance_report, generate_csv, TRACK_DAYS
        report = get_performance_report(days_back=days_back, alert_type=alert_type)

        if "error" in report:
            return f"Error: {report['error']}"

        if export_csv:
            return generate_csv(report)

        total = report.get("total_alerts", 0)
        filter_str = f" [{alert_type}]" if alert_type else ""
        lines = [f"**Watchtower Signal Performance — last {days_back} days{filter_str}**", ""]

        if total == 0:
            return "No signals logged yet. Tracking starts automatically at the next scheduled scan."

        lines.append(
            f"Signals tracked: **{total}** | Day-trade win: ≥{report.get('day_win_threshold_pct', 2)}% by next close "
            f"(intraday/news) | Swing win: ≥{report['win_threshold_pct']}%"
        )
        lines.append("")

        type_labels = {
            "intraday":  "Intraday Alerts  (track 30d)",
            "news":      "News Catalysts   (track 14d)",
            "gem":       "Hidden Gems      (track 60d)",
            "reversal":  "Reversals        (track 90d)",
            "momentum":  "Momentum         (track 90d)",
            "breakdown": "Breakdowns       (track 60d)",
            "insider":   "Insider Burst    (track 60d)",
            "master":    "Master Screen    (track 90d)",
        }

        stats = report.get("stats_by_type", {})
        for at, label in type_labels.items():
            s = stats.get(at)
            if not s:
                continue
            short_lbl = s.get("short_label", "D7")
            full_lbl = s.get("full_label", "D30")
            n_total = s["n_total"]
            n_filled = s["n_filled"]
            fill_note = f"{n_filled} fully tracked" if n_filled < n_total else "all tracked"
            lines.append(f"**{label}**  (n={n_total}, {fill_note})")
            if s.get("win_rate_short") is not None:
                lines.append(f"  Win rate {short_lbl}:   {s['win_rate_short']}%")
            if s.get("win_rate_full") is not None:
                lines.append(f"  Win rate {full_lbl}:  {s['win_rate_full']}%")
            if s.get("avg_short_return") is not None:
                lines.append(f"  Avg {short_lbl} return:  {s['avg_short_return']:+.2f}%")
            if s.get("avg_full_return") is not None:
                lines.append(f"  Avg {full_lbl} return: {s['avg_full_return']:+.2f}%")
            if s.get("avg_peak_return") is not None:
                lines.append(f"  Avg peak return: {s['avg_peak_return']:+.2f}%  ← best close before reverting")
            if s.get("best") is not None:
                lines.append(f"  Best / Worst:    {s['best']:+.2f}% / {s['worst']:+.2f}%")
            lines.append("")

        # Recent signal table — show peak so you can see move-up-then-back patterns
        rows = report.get("rows", [])[:25]
        if rows:
            lines.append("**Recent Signals** (newest first — peak% shows best close in window)")
            lines.append(f"{'Date':<12} {'Type':<11} {'Ticker':<7} {'Score':>5} {'D7%':>7} {'D30%':>7} {'Peak%':>7} {'Pk Day':>6} {'Status'}")
            lines.append("-" * 78)
            for r in rows:
                lines.append(
                    f"{r['date']:<12} {r['type']:<11} {r['ticker']:<7} "
                    f"{r['score']:>5} {r['d7%']:>7} {r['d30%']:>7} "
                    f"{r['peak%']:>7} {r['peak_day']:>6}  {r['status']}"
                )

        lines.append("")
        lines.append("Use export_csv=True for the full dataset with all d-columns.")
        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


# ============================================================
# Market Themes board + Early-Turn radar — the SAME snapshots the dashboard
# Themes tab and Hidden-Gems radar read, so Grok tells the same story the UI does.
# ============================================================
_THEME_WIN = {"1w": "r1w", "2w": "r2w", "1m": "r1m", "3m": "r3m", "6m": "r6m", "ytd": "rytd"}
_WIN_ORDER = [("1w", "1W"), ("2w", "2W"), ("1m", "1M"), ("3m", "3M"), ("6m", "6M"), ("ytd", "YTD")]


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{'+' if v >= 0 else ''}{v * 100:.1f}%"


def _fmt_vol(v) -> str:
    return f"{float(v):.1f}×" if v is not None else "—"


def _fmt_cap(mc) -> str:
    if not mc:
        return "—"
    mc = float(mc)
    return f"${mc / 1e9:.1f}B" if mc >= 1e9 else f"${mc / 1e6:.0f}M"


@mcp.tool()
def watchtower_get_themes(window: str = "1w", weight: str = "median") -> str:
    """
    Market Themes board — thematic baskets (Nuclear, Quantum, Semiconductors, AI,
    Data Centers, Fintech, Healthcare, Space, Drones, etc.) ranked by basket
    performance. The SAME data shown on the dashboard Themes tab.

    Each theme shows the full window ladder (1W/2W/1M/3M/6M/YTD), a basket
    volume-surge read (median member's last-week volume vs prior month; >1.0 =
    volume picking up), member count, and the YTD leaders.

    How to read it (trajectory across windows):
    - 1W > 2W  → accelerating / fresh turn (emerging)
    - 2W >> 1W → decelerating / front-loaded (late, cooling)
    - 1W ≈ 2W, both strong → durable trend (highest conviction)
    Then pair with volume: a turn on rising volume is real; on light volume it's
    unconfirmed. Use watchtower_get_theme_members to see what's driving a theme.

    Args:
        window: window that ranks the board — 1w | 2w | 1m | 3m | 6m | ytd (default 1w).
        weight: 'median' (typical member / breadth) or 'cap' (cap-weighted index).
    """
    from screen.reversal_screen import _conn
    win = _THEME_WIN.get((window or "1w").lower(), "r1w")
    wkey = (window or "1w").lower() if (window or "1w").lower() in _THEME_WIN else "1w"
    suffix = "cap" if (weight or "median").lower() == "cap" else "med"
    stems = ["r1w", "r2w", "r1m", "r3m", "r6m", "rytd"]
    cols = ", ".join(f"{s}_{suffix}" for s in stems)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT theme, n, {cols}, vol_med, leaders, max(as_of) OVER () "
                f"FROM theme_performance ORDER BY {win}_{suffix} DESC NULLS LAST"
            )
            rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not rows:
        return "No theme data yet — the Themes snapshots refresh with the daily ingestion."
    as_of = rows[0][-1]  # max(as_of) OVER () is the final SELECT column ([9] was `leaders`)
    lines = [
        f"**MARKET THEMES — ranked by {wkey.upper()} "
        f"({'cap-weighted' if suffix == 'cap' else 'median'}), as of {as_of}**",
        "*Trajectory: 1W>2W = accelerating (early turn); 2W>>1W = decelerating (late); "
        "1W≈2W & strong = durable. vol >1.0 = volume picking up.*\n",
    ]
    for i, row in enumerate(rows, 1):
        theme, n = row[0], row[1]
        vals = row[2:8]
        vol, leaders = row[8], row[9] or []
        head = dict(zip([k for k, _ in _WIN_ORDER], vals)).get(wkey)
        ladder = " · ".join(f"{lbl} {_fmt_pct(v)}" for (k, lbl), v in zip(_WIN_ORDER, vals))
        lines.append(
            f"{i}. **{theme}** {_fmt_pct(head)} ({wkey.upper()}) | vol {_fmt_vol(vol)} | {n} names\n"
            f"   {ladder} | leaders: {', '.join(leaders[:5])}"
        )
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_theme_members(theme: str, window: str = "1w", top_n: int = 30) -> str:
    """
    Drill into one theme — every stock in the basket with its per-window returns
    (1W/2W/1M/3M/6M/YTD) and volume surge, sorted by the chosen window (best
    first). Use this to see WHICH names are driving or dragging a theme.

    Args:
        theme:  theme name, fuzzy — 'nuclear' matches 'Nuclear & SMR'.
        window: sort window — 1w | 2w | 1m | 3m | 6m | ytd (default 1w).
        top_n:  how many names to show (default 30).
    """
    from screen.reversal_screen import _conn
    win = _THEME_WIN.get((window or "1w").lower(), "r1w")
    wkey = (window or "1w").lower() if (window or "1w").lower() in _THEME_WIN else "1w"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT theme FROM theme_performance")
            themes = [r[0] for r in cur.fetchall()]
            t = (theme or "").strip().lower()
            match = next((x for x in themes if x.lower() == t), None) \
                or next((x for x in themes if t and t in x.lower()), None)
            if not match:
                return "Theme not found. Available themes: " + ", ".join(sorted(themes))
            cur.execute(
                f"SELECT ticker, company_name, market_cap, r1w, r2w, r1m, r3m, r6m, rytd, vol_surge "
                f"FROM theme_member_perf WHERE theme=%s ORDER BY {win} DESC NULLS LAST",
                (match,),
            )
            rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not rows:
        return f"No members with price data for {match}."
    shown = rows[:max(1, top_n)]
    lines = [
        f"**{match} — {len(rows)} names, sorted by {wkey.upper()} "
        f"(showing top {len(shown)})**",
        "*ladder: 1W · 2W · 1M · 3M · 6M · YTD | vol = last-week volume vs prior month*\n",
    ]
    for r in shown:
        tk, co = r[0], (r[1] or "")[:26]
        ladder = " · ".join(_fmt_pct(v) for v in r[3:9])
        lines.append(f"- **{tk}** ({co}, {_fmt_cap(r[2])}) {ladder} | vol {_fmt_vol(r[9])}")
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_early_turn() -> str:
    """
    Early-Turn radar — INDUSTRIES coiling / turning up on the SHORT window
    (1–2 week strength + breadth + rising volume) BEFORE they're '3-month hot'.
    The same data as the dashboard radar. Lower-conviction by design (early =
    more head-fakes) — a watch, not a trigger.

    Each row: 1W/2W/1M/3M median returns, 2-week breadth (% of names up), volume
    surge (recent vs prior), and the names leading the turn. A fresh 2-week move
    that is broad and on rising volume, in an industry not yet 3-month hot.
    """
    from screen.reversal_screen import _conn
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT industry, sector, n, r1w_med, r2w_med, r1m_med, r3m_med, "
                "breadth_2w, vol_surge, leaders, max(as_of) OVER () "
                "FROM industry_pulse WHERE state='early_turn' "
                "ORDER BY early_score DESC LIMIT 20"
            )
            rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not rows:
        return "No industries are flagged as early-turn in the latest snapshot."
    as_of = rows[0][10]
    lines = [
        f"**EARLY-TURN RADAR — {len(rows)} industries coiling, as of {as_of}** (a watch, not a trigger)",
        "*Fresh 1–2 week strength + broad breadth + rising volume, not yet 3-month hot.*\n",
    ]
    for r in rows:
        ind, sec, n = r[0], r[1], r[2]
        breadth = f"{float(r[7]) * 100:.0f}%" if r[7] is not None else "—"
        leaders = r[9] or []
        lines.append(
            f"- **{ind}** ({sec}, n={n}) — 1W {_fmt_pct(r[3])} · 2W {_fmt_pct(r[4])} · "
            f"1M {_fmt_pct(r[5])} · 3M {_fmt_pct(r[6])} | breadth {breadth} | "
            f"vol {_fmt_vol(r[8])} | turning: {', '.join(leaders[:4])}"
        )
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_bearish_rotation(top_n: int = 10, with_put_chains: bool = False) -> str:
    """
    Early BEARISH rotation — the mirror of the early-turn radar.

    Three reads in one call:
    1. Cooling industries: 2-week median <= -4% while the 3-month is still
       positive ("was working, now cracking"), ranked by a fade score that
       weights decline-on-volume (distribution) and breadth collapse.
    2. Risk gauge: defensives (GLD/XLU/XLP/XLV) vs offense (XLK/XLY/QQQ/SMH/
       IWM) weekly spread + HYG credit read + market regime — tells you if
       the cooling is risk-off rotation or sector noise.
    3. Put candidates: weak, liquid names ($2B+ cap) inside the cooling
       groups, flagged when a live bearish chart pattern backs the read.

    Args:
        top_n:           max cooling industries / candidates shown (default 10)
        with_put_chains: fetch live put chains for the top 3 candidates
                         (Polygon options — slower)
    """
    try:
        from dashboard.api import _bearish_rotation_rows
        from analysis.pattern_scan import PATTERN_NAMES
        data = _bearish_rotation_rows()
        rk = data.get("risk") or {}
        lines = ["**Early Turn ↓ — bearish rotation radar**", ""]
        if rk.get("state"):
            lbl = {"risk_off": "RISK-OFF", "risk_on": "RISK-ON",
                   "neutral": "NEUTRAL"}.get(rk["state"], rk["state"])
            lines.append(
                f"Risk gauge: **{lbl}** (defensives {rk.get('defensive_w', 0):+.1f}% vs "
                f"offense {rk.get('offense_w', 0):+.1f}% weekly, spread "
                f"{rk.get('spread', 0):+.1f}pt) · SPY {rk.get('spy_w', 0):+.1f}% · "
                f"HYG {rk.get('hyg_w', 0):+.1f}%"
                + (f" · regime: {rk['regime']}" if rk.get("regime") else ""))
            lines.append("")
        cooling = (data.get("cooling") or [])[:top_n]
        if cooling:
            lines.append(f"**Cooling industries** ({len(cooling)}):")
            for c in cooling:
                lines.append(
                    f"- **{c['industry']}** ({c['sector']}, {c['n']} names) — "
                    f"2wk {c['r2w']*100:+.1f}% / 3mo {c['r3m']*100:+.1f}%, "
                    f"breadth {c['breadth']*100:.0f}%, vol {c['vol_surge']:.2f}x "
                    f"· fade {c['fade_score']:.0f}")
            lines.append("")
        puts = (data.get("puts") or [])[:top_n]
        if puts:
            lines.append("**Put candidates** (weak + liquid inside cooling groups):")
            for p in puts:
                pat = ""
                if p.get("bear_patterns"):
                    nm = PATTERN_NAMES.get(p.get("top_pattern"), p.get("top_pattern") or "")
                    pat = (f" · ▼ {nm} ({p.get('top_tf')}, {p.get('top_status')}"
                           + (f", +{p['bear_patterns']-1} more" if p["bear_patterns"] > 1 else "")
                           + ")")
                lines.append(
                    f"- **{p['ticker']}** {p['company_name']} — {p['industry']} · "
                    f"RS {p['rs_pct']} · 1mo {p['ret_1m']*100:+.1f}% · "
                    f"vs base {p['vs_sma']*100:+.1f}% · ${p['price']:,.2f} · "
                    f"weak {p['weak_score']:.0f}{pat}")
        if not cooling and not puts:
            lines.append("No cooling industries right now — nothing rolling over early.")
        if with_put_chains and puts:
            from analysis.polygon_data import fetch_options_snapshot
            lines.append("")
            lines.append("**Live put chains (top 3):**")
            for p in puts[:3]:
                snap = fetch_options_snapshot(p["ticker"])
                pl = snap.get("puts") or []
                if pl:
                    ps = ", ".join(
                        f"{q.get('expiration','?')} ${q.get('strike')}p @ ${q.get('last_price')}"
                        for q in pl[:4])
                    lines.append(f"- {p['ticker']}: {ps}")
                else:
                    lines.append(f"- {p['ticker']}: no chain returned "
                                 f"({snap.get('note') or snap.get('error') or 'n/a'})")
        lines.append("")
        lines.append("_Reminder: puts punish lateness — this list is for finding "
                     "setups while IV is still cheap. Confirm IV/spreads before "
                     "buying premium._")
        return "\n".join(lines)
    except Exception as e:
        return f"Error building bearish rotation: {e}"


@mcp.tool()
def watchtower_get_sector_heatmap(timeframe: str = "quarterly", weight: str = "median") -> str:
    """
    Sector Heat Map — all 11 GICS sectors ranked hottest→coldest by price
    momentum over a timeframe. The SAME map shown on the dashboard (Hidden Gems
    tab). Use this for "how are the sectors doing" / "sector heat map" questions.

    Each sector shows the MEDIAN stock return (breadth — the typical name) and
    the CAP-WEIGHTED return (the sector-index / ETF view, mega-cap driven). The
    gap between them is a breadth signal: median > cap = broad rally beyond the
    giants; cap > median = a few mega-caps carrying the sector.

    Args:
        timeframe: daily | weekly | monthly | quarterly (default quarterly).
                   monthly ≈ last ~30 days rolling, quarterly ≈ ~91 days.
        weight: which number ranks/labels — 'median' (breadth) or 'cap' (index).
    """
    from dashboard.api import _sector_heat_live, _HEAT_WINDOWS
    tf = (timeframe or "quarterly").lower()
    if tf not in _HEAT_WINDOWS:
        tf = "quarterly"
    wt = "cap" if (weight or "median").lower() == "cap" else "median"
    rows = _sector_heat_live(tf, wt)
    if not rows:
        return "No sector heat data available."
    lines = [
        f"**SECTOR HEAT MAP — {tf}, ranked by {wt} (hottest→coldest)**",
        "*median = the typical stock (breadth); cap = cap-weighted index (mega-cap "
        "driven). median > cap = broad participation.*\n",
    ]
    for r in rows:
        lines.append(
            f"{r['rank']}. **{r['sector']}** {_fmt_pct(r.get('ret'))} ({wt}) | "
            f"median {_fmt_pct(r.get('median_ret'))} · cap {_fmt_pct(r.get('capwtd_ret'))} | "
            f"{r['n']} names"
        )
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_sector_rotation() -> str:
    """
    Sector Rotation read — which GICS sectors are seeing early money inflow vs
    outflow, with the market regime and a narrative. The SAME data as the
    dashboard Sector Rotation card. Median (breadth) is the primary signal;
    a 'breadth-led' (★) move is the earliest kind — many names participating,
    not just the cap-weighted index.
    """
    from dashboard.api import _rotation_rows
    d = _rotation_rows()
    if not d or not d.get("sectors"):
        return "No sector-rotation read available yet."
    lines = [
        f"**SECTOR ROTATION — as of {d.get('as_of')}**"
        + (f" (regime: {d['regime']})" if d.get("regime") else "")
    ]
    if d.get("rotating_in"):
        lines.append(f"Rotating IN:  {', '.join(d['rotating_in'])}")
    if d.get("rotating_out"):
        lines.append(f"Rotating OUT: {', '.join(d['rotating_out'])}")
    if d.get("narrative"):
        lines.append(f"\n{d['narrative']}\n")
    lines.append("Per sector (★ = breadth-led, the earliest signal):")
    for s in d["sectors"]:
        star = " ★" if s.get("breadth_led") else ""
        lines.append(
            f"- **{s['sector']}** [{s.get('state', '')}{star}] — "
            f"1W {_fmt_pct(s.get('week_ret'))} · 1M {_fmt_pct(s.get('month_ret'))} · "
            f"3M {_fmt_pct(s.get('qtr_ret'))}"
        )
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_gem_performance() -> str:
    """
    Track record of the Hidden-Gem picks — forward returns of every gem pick at
    7 / 30 / 90 days, overall and by sleeve (momentum vs reversal), with average
    return, win rate, and sample size. The SAME data as the dashboard Performance
    tab gem panel. Horizons that haven't fully elapsed show n=0 (still maturing).
    """
    from dashboard.api import _gem_performance
    d = _gem_performance()
    n = d.get("total_picks", 0)
    if not n:
        return "No gem picks tracked yet."

    def fmt_row(label, g):
        def cell(h):
            c = g.get(h, {}) or {}
            if not c.get("n"):
                return f"{h[1:].upper()}d: — (maturing)"
            return (f"{h[1:].upper()}d: avg {_fmt_pct(c.get('avg'))}, "
                    f"win {(c.get('win') or 0) * 100:.0f}% (n={c['n']})")
        return f"{label}: " + " | ".join(cell(h) for h in ("d7", "d30", "d90"))

    lines = [
        f"**HIDDEN-GEM PICK PERFORMANCE — {n} picks "
        f"({d.get('first_day')} → {d.get('last_day')})**",
        "*forward return from each pick's entry price; win = % of picks positive.*\n",
    ]
    for o in d.get("overall", []):
        lines.append(fmt_row("Overall", o))
    for s in d.get("by_sleeve", []):
        lines.append(fmt_row((s.get("group") or "?").capitalize(), s))
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_gem_departures() -> str:
    """
    Why names dropped off the Hidden Gems list at the latest scan. A name can
    leave for very different reasons, and this says which: its industry rotated
    out of the hot-bottleneck set (e.g. AAL when airlines cool), the stock got
    too extended or broke its 30-week base, it blew off (>100% in 6mo), its size
    left the $100M-$10B band, or it simply missed the day's top-N cutoff
    (out-ranked, still eligible). The SAME data as the dashboard "Recently
    dropped" card — use it to answer "why did <ticker> fall off the gem list?".
    """
    from dashboard.api import _gem_departures
    d = _gem_departures()
    rows = d.get("rows") or []
    if not rows:
        return "No gem drop-offs recorded for the latest scan."
    label = {
        "industry_cooled": "industry cooled", "out_ranked": "out-ranked (missed cutoff)",
        "too_extended": "too extended", "broke_30w_base": "broke its base",
        "blew_off": "blew off", "size_out_of_band": "size out of band",
        "left_universe": "left universe",
    }
    lines = [
        f"**HIDDEN-GEM DROP-OFFS — {d.get('as_of')}** ({len(rows)} names left the list since the prior scan)",
        "*A name leaving doesn't always mean it weakened — 'industry cooled' is sector rotation.*\n",
    ]
    for r in rows:
        sc = f"{r['prev_score']:.0f}" if r.get("prev_score") is not None else "—"
        lines.append(
            f"- **{r['ticker']}** (was score {sc}, {r.get('sleeve') or ''}) — "
            f"{label.get(r.get('reason'), r.get('reason'))}: {r.get('detail') or ''}"
        )
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_vantage(metric: str = "pe", universe: str = "ALL",
                           mincap: str = "2b", top_n: int = 15) -> str:
    """
    Vantage — the fundamentals / valuation map. Ranks stocks by a single
    fundamental metric and returns the best and worst names on it. The SAME data
    as the dashboard Vantage tab. Use for "cheapest semis by P/E", "highest ROE
    in energy", "best Piotroski scores", etc.

    Metrics:
      valuation (lower = cheaper): pe, forward_pe, ps, ev_ebitda, pb
      quality/growth (higher = better): fcf_yield, roe, roic, gross_margin,
        operating_margin, rev_growth
      health: piotroski (0–9 F-score), altman_z (bankruptcy distance)

    Args:
        metric: one of the above (default pe).
        universe: 'ALL' or a GICS sector (e.g. 'Technology', 'Energy').
        mincap: market-cap floor — '2b' | '500m' | '250m' | 'all' (default 2b).
        top_n: names per end of the ranking (default 15).
    """
    from dashboard.api import _vantage_rows, _VANTAGE_METRICS, _VANTAGE_MINCAPS
    m = (metric or "pe").lower()
    if m not in _VANTAGE_METRICS:
        return "Unknown metric. Available: " + ", ".join(sorted(_VANTAGE_METRICS))
    cap = _VANTAGE_MINCAPS.get((mincap or "2b").lower(), 2e9)
    uni = (universe or "ALL").strip() or "ALL"
    limit = 400 if uni == "ALL" else 1500
    d = _vantage_rows(m, uni, "abs", cap, limit)
    tiles = d.get("tiles") or []
    if not tiles:
        err = f" ({d['error']})" if d.get("error") else ""
        return f"No Vantage data for {d.get('metric_label', m)}" + (f" / {uni}" if uni != "ALL" else "") + err
    lower = d.get("lower_is_better")
    n = max(1, top_n)

    def line(t):
        return (f"- **{t['ticker']}** ({(t.get('company') or '')[:22]}, "
                f"{t.get('sector', '')}, {_fmt_cap(t.get('market_cap'))}) {t.get('display')}")
    lines = [
        f"**VANTAGE — {d.get('metric_label')} | {uni} | cap≥{(mincap or '2b').upper()} | "
        f"as of {d.get('as_of')}** ({d.get('count')} names ranked)",
        f"\n{'Cheapest' if lower else 'Highest'} (best):",
    ]
    lines += [line(t) for t in tiles[:n]]
    lines.append(f"\n{'Priciest' if lower else 'Lowest'}:")
    lines += [line(t) for t in tiles[-n:][::-1]]
    return "\n".join(lines)


@mcp.tool()
def watchtower_get_screener(sector: str = "ALL", sort: str = "score", cap: str = "gem",
                            gems_only: bool = False, industry: str = "",
                            search: str = "", top_n: int = 25) -> str:
    """
    Screener — the full gem-gate stock pool, filterable. The SAME data as the
    dashboard Screener tab: every name that clears the gem gates, with returns,
    fundamentals, and (when present) its live gem score / theme / sleeve.

    Args:
        sector: 'ALL' or a GICS sector (e.g. 'Technology', 'Energy', 'Industrials').
        sort: score | rs | 1m | 3m | 6m | rev | mktcap | ticker (default score).
            'rs' = relative-strength percentile (1-99, momentum vs the whole
            screenable universe; 99 = top).
        cap: market-cap band — 'gem' (<$10B, default), 'large' (<$50B), 'all'.
        gems_only: True = only names currently flagged as hidden gems.
        industry: optional exact GICS industry filter (e.g. 'Uranium', 'Biotechnology').
        search: match a ticker or company name (e.g. 'HY', 'hyster') — finds a
            name even if it ranks past the default row limit.
        top_n: rows to return (default 25).
    """
    from dashboard.api import _screener_rows, _SCREENER_SORTS, _SCREENER_CAPS
    s = (sort or "score").lower()
    if s not in _SCREENER_SORTS:
        s = "score"
    c = (cap or "gem").lower()
    if c not in _SCREENER_CAPS:
        c = "gem"
    d = _screener_rows(sector or "ALL", s, bool(gems_only), c, industry or "", search or "")
    rows = d.get("rows") or []
    if not rows:
        return "No names match that screen."
    shown = rows[:max(1, top_n)]
    lines = [
        f"**SCREENER — sector={sector or 'ALL'}, cap={c}, sort={s}"
        + (f", industry={industry}" if industry else "")
        + (", gems only" if gems_only else "")
        + f"** ({d.get('total')} in pool, showing {len(shown)})",
        "*per name: 1M·3M·6M return | rev YoY, gross margin, Piotroski, Altman-Z | gem score*\n",
    ]
    for r in shown:
        rs = f"RS {r['rs_pct']} · " if r.get("rs_pct") is not None else ""
        flags = ""
        if r.get("is_parabolic"):
            flags += " 🔥parabolic"
        if r.get("is_recent_ipo"):
            flags += " [recent IPO]"
        rets = (f"{rs}1M {_fmt_pct(r.get('ret_1m'))} · 3M {_fmt_pct(r.get('ret_3m'))} · "
                f"6M {_fmt_pct(r.get('ret_6m'))}{flags}")
        fund = []
        if r.get("rev_yoy") is not None:
            fund.append(f"rev {_fmt_pct(r['rev_yoy'])}")
        if r.get("gross_margin") is not None:
            fund.append(f"GM {_fmt_pct(r['gross_margin'])}")
        if r.get("piotroski") is not None:
            fund.append(f"Piotroski {r['piotroski']}/9")
        if r.get("altman_z") is not None:
            fund.append(f"Z {float(r['altman_z']):.1f}")
        gem = ""
        if r.get("gem_score") is not None:
            theme = f" · {r['gem_theme']}" if r.get("gem_theme") else ""
            gem = f" | GEM {float(r['gem_score']):.0f} ({r.get('gem_sleeve') or ''}{theme})"
        lines.append(
            f"- **{r['ticker']}** ({(r.get('company_name') or '')[:22]}, "
            f"{r.get('sector', '')}, {_fmt_cap(r.get('market_cap'))}) {rets} | "
            f"{', '.join(fund)}{gem}"
        )
    return "\n".join(lines)


# NOTE: /.well-known endpoints intentionally omitted.
# When they exist, Grok auto-discovers OAuth and tries to run the flow via its
# server-side connector manager (not a browser), which can't do the redirect.
# Without them, Grok falls back to showing the manual OAuth credentials form,
# which lets the user fill in /authorize and /token — and the browser-based
# redirect flow works correctly.


@mcp.custom_route("/authorize", methods=["GET"])
async def authorize(request: Request):
    """OAuth authorization endpoint — auto-approves for ALLOWLISTED client
    callbacks and redirects back with a one-time code. PKCE (code_challenge)
    is recorded here and enforced at /token when presented."""
    params = dict(request.query_params)
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    challenge_method = (params.get("code_challenge_method") or "plain").upper()

    if not redirect_uri:
        return JSONResponse({"error": "missing redirect_uri"}, status_code=400)
    if not _redirect_allowed(redirect_uri):
        return JSONResponse(
            {"error": "unauthorized_redirect_uri",
             "error_description": f"redirect_uri not in allowlist: {redirect_uri}. "
                                  "Add its https origin prefix to the "
                                  "OAUTH_REDIRECT_ALLOW env var if this is a "
                                  "legitimate new MCP client."},
            status_code=400)

    # housekeeping: drop expired codes so the dict can't grow unboundedly
    now = time.time()
    for c in [c for c, v in _auth_codes.items() if v[1] < now]:
        _auth_codes.pop(c, None)

    code = secrets.token_urlsafe(32)
    _auth_codes[code] = (redirect_uri, now + 300, code_challenge, challenge_method)

    sep = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{sep}code={code}"
    if state:
        location += f"&state={state}"

    return RedirectResponse(location, status_code=302)


@mcp.custom_route("/token", methods=["POST"])
async def token(request: Request):
    """OAuth token endpoint — exchanges a one-time code for a session Bearer
    token. Enforces PKCE when the client supplied a code_challenge at
    /authorize (Claude does; Grok's manual-credentials flow may not — PKCE is
    enforced-when-offered so both keep working). Returns an HMAC-derived
    session token, never MCP_AUTH_TOKEN itself."""
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

    redirect_uri, expires_at, code_challenge, challenge_method = entry
    if time.time() > expires_at:
        return JSONResponse({"error": "invalid_grant", "error_description": "code expired"}, status_code=400)

    # redirect_uri binding (RFC 6749 §4.1.3): enforced when the client sends it.
    sent_redirect = data.get("redirect_uri", "")
    if sent_redirect and sent_redirect != redirect_uri:
        return JSONResponse({"error": "invalid_grant",
                             "error_description": "redirect_uri mismatch"}, status_code=400)

    # PKCE (RFC 7636): if a challenge was registered with the code, the
    # matching verifier is REQUIRED — this is what stops a stolen/forged code
    # from being exchanged by anyone other than the client that started the flow.
    if code_challenge:
        verifier = data.get("code_verifier", "")
        if not verifier:
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "code_verifier required"}, status_code=400)
        if challenge_method == "S256":
            digest = hashlib.sha256(verifier.encode("ascii", errors="replace")).digest()
            computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        else:  # "plain"
            computed = verifier
        if not hmac.compare_digest(computed, code_challenge):
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "PKCE verification failed"}, status_code=400)

    return JSONResponse({
        "access_token": _mcp_session_token(),
        "token_type": "bearer",
        "expires_in": 315360000,  # ~10 years — effectively permanent
    })


# ── Health check ──────────────────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({
        "status": "ok",
        "service": "watchtower-mcp",
        "version": "1.2.0-dashboard",
        "dashboard": "/dashboard",
        "tools": [
            "watchtower_run_screen",
            "watchtower_intraday_scan",
            "watchtower_analyze_ticker",
            "watchtower_fair_value",
            "watchtower_get_momentum",
            "watchtower_get_bearish_ideas",
            "watchtower_get_hidden_gems",
            "watchtower_get_social_buzz",
            "watchtower_get_gmmss_context",
            "watchtower_force_scan",
            "watchtower_alert_performance",
        ],
    })


# ── Live dashboard (web UI + JSON API) ───────────────────────────────────────
# Served from the same app: GET /dashboard plus /api/* endpoints.
# Auth via DASHBOARD_PASSWORD (falls back to MCP_AUTH_TOKEN).

try:
    from dashboard.api import register_routes as _register_dashboard
    _register_dashboard(mcp)
except Exception as _dash_err:
    import logging as _dash_logging
    _dash_logging.getLogger(__name__).warning(
        f"[server] Dashboard failed to register (UI disabled): {_dash_err}"
    )


# ── Scheduler startup ────────────────────────────────────────────────────────
# Guard: only start the scheduler in the first worker process.
# Railway / uvicorn can run multiple replicas — each would start its own
# scheduler causing every job to fire N times. A PID file ensures only one
# process owns the scheduler per container.

import logging
import os
import tempfile

logging.basicConfig(level=logging.INFO)

_SCHEDULER_LOCK = os.path.join(tempfile.gettempdir(), "watchtower_scheduler.lock")
_scheduler = None

def _is_scheduler_owner() -> bool:
    """Return True if this process should own the scheduler."""
    try:
        if os.path.exists(_SCHEDULER_LOCK):
            pid = int(open(_SCHEDULER_LOCK).read().strip())
            try:
                os.kill(pid, 0)  # check if that PID is still alive
                return False     # another process owns it
            except OSError:
                pass  # PID is dead — take ownership
        with open(_SCHEDULER_LOCK, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return True  # default to starting if lock check fails

if _is_scheduler_owner():
    try:
        from alerts.scheduler import start_scheduler
        _scheduler = start_scheduler()
        logging.info(f"[server] Scheduler started by PID {os.getpid()}.")
    except Exception as _sched_err:
        logging.warning(f"[server] Scheduler failed to start (alerts disabled): {_sched_err}")
else:
    logging.info(f"[server] Scheduler already running in another worker — skipping.")


# ── ASGI app with Bearer auth on /mcp ─────────────────────────────────────────

raw_app = mcp.streamable_http_app()


class AuthASGIWrapper:
    """Lightweight ASGI wrapper — enforces Bearer auth on /mcp, passes OAuth paths through.

    Also rewrites the Host header to 'localhost' for /mcp requests so the MCP SDK's
    built-in DNS-rebinding protection doesn't reject Railway's public hostname (421).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if MCP_AUTH_TOKEN and path.startswith("/mcp") and path not in PUBLIC_PATHS:
                headers = dict(scope.get("headers", []))
                host = headers.get(b"host", b"").decode("utf-8", errors="replace")
                base_url = f"https://{host}" if host else ""
                auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
                if not auth.lower().startswith("bearer "):
                    await self._unauthorized(send, base_url)
                    return
                token = auth.split(" ", 1)[1].strip()
                # Accept the master token (direct/manual configs) or the
                # HMAC-derived session token the OAuth flow now hands out.
                # Existing clients that cached the raw master token before the
                # hardening keep working; new OAuth exchanges never see it.
                valid = (hmac.compare_digest(token, MCP_AUTH_TOKEN)
                         or hmac.compare_digest(token, _mcp_session_token()))
                if not valid:
                    await self._unauthorized(send, base_url)
                    return

        await self.app(scope, receive, send)

    async def _unauthorized(self, send, base_url: str = ""):
        body = b'{"error":"Unauthorized"}'
        resource_metadata = f"{base_url}/.well-known/oauth-protected-resource"
        www_auth = f'Bearer realm="watchtower", resource_metadata="{resource_metadata}"'
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", www_auth.encode()),
        ]})
        await send({"type": "http.response.body", "body": body})


app = AuthASGIWrapper(raw_app)


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("server:app", host=host, port=PORT, log_level="info")
