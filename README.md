# Watchtower MCP Server (for Grok custom connectors)

Full GMMSS (Grok Multi-Regime Multi-Sleeve System) exposed as MCP tools.

This is the dedicated service/repo you use for the Grok UI "Add custom connector"
path so the system is available on web, other computers, and mobile (the same
setup you previously used with Claude).

## Current tools (after GMMSS expansion)

- watchtower_get_gmmss_context (best starting point for interactive chat)
- watchtower_get_regime (current bull/bear + dynamic weights + gross target)
- watchtower_get_momentum (Sleeve 2 up-and-comers + sector heat + RS + live Polygon)
- watchtower_get_bearish_ideas (Sleeve 3 breakdowns/puts — always surfaced)
- watchtower_research (full Phase 3 + GMMSS theses with sleeve/regime/10x framing)
- watchtower_get_daily_report (regime + sleeves + brief)
- watchtower_run_screen (reversal | momentum | breakdown | ...)
- watchtower_get_methodology (full rules + expectations)
- watchtower_phase3_stats, watchtower_get_sleeve_performance, etc.

## Railway deployment (the one powering your custom connector)

1. Push updates here (server.py, requirements.txt, supporting screen/analysis/signals modules).
2. In Railway dashboard, open the **watchtower-mcp** service.
3. Go to the **Variables** tab.
4. Add / update these (copy the values from your local watchtower/.env):
   - POLYGON_API_KEY (critical for live momentum, bearish signals, regime, RS, vol surge in the connector tools)
   - XAI_API_KEY (for research/theses inside get_daily_report and watchtower_research)
   - All SUPABASE_DB_* (for live screen fallbacks when no daily artifacts)
   - FMP_API_KEY
   - RESEND_* (optional)
   - MCP_AUTH_TOKEN (strong value; clients must send Bearer or X-MCP-Token)
   - MCP_HOST=0.0.0.0
5. Trigger a redeploy (or new commit will auto-deploy).

After deploy, use the public URL (e.g. https://watchtower-mcp-production-....up.railway.app/mcp)
when adding the custom connector in Grok settings.

Note on the Grok UI connector form: It expects OAuth/PKCE. Our server uses simple
Bearer token auth (or none during initial setup). Common flow:
- Temporarily blank MCP_AUTH_TOKEN in Railway vars + redeploy so the "test call"
  during Save & Connect succeeds.
- Fill Client ID (any string e.g. watchtower-mcp), pick "none (PKCE only, recommended)".
- After the connector is saved/connected, re-add the MCP_AUTH_TOKEN var and edit the
  saved connector to supply a custom header: Authorization: Bearer <your-token>.

The 4am local scheduled job continues to produce the current_*.csv artifacts that
power many responses; the live fallbacks make the Railway connector immediately useful
for any stock even without those artifacts present on the cloud instance.
