WATCHTOWER-MCP RAILWAY ENVIRONMENT VARIABLES

This file is for the dedicated 'watchtower-mcp' Railway service (the one whose public URL you add as a Grok custom connector for everywhere access).

IMPORTANT: These variables are set in the Railway dashboard UI for the SPECIFIC 'watchtower-mcp' service.
They are NOT in this GitHub repo (for security) and are NOT the same as any 'watchtower' main service you may have.

The code in this repo (server.py + screen/ + analysis/ + signals/) expects these to be present at runtime for full live GMMSS functionality on the custom connector.

After changing any variables, trigger a redeploy on the watchtower-mcp service.

--- REQUIRED FOR FULL LIVE CUSTOM CONNECTOR ---

1. POLYGON_API_KEY
   - Purpose: Live bars, volume surge, RS vs SPY, SPY regime (200MA), options snapshots for bearish/put sleeve.
   - Required for: watchtower_get_momentum, watchtower_get_bearish_ideas, watchtower_get_regime, watchtower_research (rich theses), watchtower_get_gmmss_context live fallbacks, watchtower_run_screen (momentum|breakdown).
   - Without this the connector falls back to stale DB data or limited yfinance and loses the 'live technicals' power you have locally.
   - Value: Copy from your local watchtower/.env (never paste the actual key into this file)

2. XAI_API_KEY
   - Purpose: Powers Grok synthesis / theses inside watchtower_research and the Grok section of watchtower_get_daily_report.
   - Value: Copy from your local watchtower/.env (the xai-... key)

3. Full Supabase direct DB connection (for screens when no artifacts present on Railway)
   SUPABASE_DB_HOST
   SUPABASE_DB_PORT
   SUPABASE_DB_USER
   SUPABASE_DB_PASSWORD
   SUPABASE_DB_NAME
   - These are the pooler/direct ones (aws-...pooler.supabase.com etc.), not just the publishable/secret from the API keys page.
   - Value: Copy exact from your local watchtower/.env (never paste the actual password into this file)

4. FMP_API_KEY
   - Some enrichment / universe expansion paths.
   - Value: Copy from your local watchtower/.env (never paste the actual key into this file)

5. RESEND_API_KEY + RESEND_FROM + ALERT_EMAIL_TO (optional but recommended if any report/email paths are exercised from the connector)

6. MCP_AUTH_TOKEN (strongly recommended for public Railway URL)
   - Generate a strong random value (or reuse the one you had for the old Claude connector).
   - Clients (including the Grok custom connector) must send it as:
     Authorization: Bearer <token>
     or
     X-MCP-Token: <token>
   - In the Grok UI custom connector setup, after adding, edit the saved connector to inject this header.

7. MCP_HOST=0.0.0.0
   - Required so Railway binds correctly (the code already honors $PORT).

--- HOW TO SET ---
1. Go to Railway dashboard.
2. Select the exact 'watchtower-mcp' service (the one for the custom connector, not any main 'watchtower' service).
3. Go to the Variables tab.
4. Add New Variable for each of the above (or bulk paste if the UI allows).
5. Save.
6. Redeploy the service.

--- DISTINCTION FROM MAIN WATCHTOWER ---
- Main 'watchtower' repo + any corresponding Railway service is for your local 4am job, full ingests, daily_email.py, etc.
- This watchtower-mcp repo/service is the slim dedicated one for the Grok custom connector 'everywhere' experience.
- Keys can (and should) be set independently on the mcp service.
- The .env.example in this repo is a template you can reference when filling the Railway Variables for the mcp service.

Once these are set and the service is redeployed, your activated custom connector should have full live Polygon-powered GMMSS tools (momentum/up-and-comers with sector heat + RS + vol surge, always-visible bearish sleeve, accurate regime, rich research theses, etc.).

Test in the connector with:
  watchtower_get_gmmss_context
  watchtower_get_momentum
  watchtower_get_bearish_ideas
  watchtower_get_regime
  watchtower_research with some tickers
