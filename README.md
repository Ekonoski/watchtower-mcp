# Watchtower MCP — Live Scanner Service

The always-on Railway service for the Watchtower trading platform:

1. **Live dashboard** at `/dashboard` — the primary interface. Auto-surfaces
   day-trading setups across the full US market with news catalysts, X/social
   sentiment, price/volume/% change, and browser pop-up alerts.
2. **MCP server** at `/mcp` — exposes every screen as a tool for Grok, Claude,
   and other MCP clients (Bearer-token auth).
3. **Background scheduler** — scans every 5 minutes during market hours,
   every 15 minutes pre-market, plus daily screen sweeps; persists every scan
   and sends gated email alerts.

## Dashboard

Open `https://<your-railway-domain>/dashboard`.

- **Scanner tab** — every intraday signal from the latest scan (GAP_AND_GO,
  VWAP_BREAKOUT, FLUSH_REVERSAL, breakdowns, volume surges…) with score,
  price, % change, gap %, volume pace, VWAP side, and sector. Filter by
  direction/score/ticker, sort any column, click a row for a live drill-down
  with X sentiment.
- **News Catalysts tab** — Grok-classified market-moving news (earnings, FDA,
  M&A, analyst actions…) cross-referenced with live technicals into
  BUY/WATCH/AVOID calls. Off-radar tickers flagged.
- **Performance tab** — win rates and return paths (d1→d90) for every signal
  type ever fired, straight from `alert_log`.
- **Watchlist tab** — manage the tickers that are always included in scans.
- **🔔 Alerts** — browser pop-up notifications when new signals (score ≥ 55)
  or high-impact news land. The page polls every 30 s; scans run every 5 min.

### Dashboard auth

Set `DASHBOARD_PASSWORD` in Railway variables (falls back to
`MCP_AUTH_TOKEN`). Session lives in an HttpOnly cookie for 30 days.

## Scan → alert flow

```
every 5 min (mkt hours)        every scan              gated
┌──────────────────┐   ┌───────────────────────┐   ┌──────────────┐
│ intraday screen  │ → │ scan_snapshots (DB)   │ → │ email alert  │
│ news scan (Grok) │   │ + alert_log tracking  │   │ fresh signals│
│ social pulse (X) │   │ → dashboard reads this │   │ only, 60-min │
└──────────────────┘   └───────────────────────┘   │ per-ticker   │
                                                   │ cooldown     │
                                                   └──────────────┘
```

Email gating knobs (Railway variables):

| Variable | Default | Meaning |
|---|---|---|
| `ALERT_EMAIL_MIN_SCORE` | `55` | Min intraday score to count as email-worthy |
| `ALERT_EMAIL_COOLDOWN_MIN` | `60` | Per-ticker email cooldown |
| `ALERT_EMAIL_HEARTBEAT_MIN` | `60` | Max minutes between emails during scans |
| `EMAIL_EVERY_SCAN` | unset | `true` restores email-on-every-scan |
| `POLYGON_DATA_DELAY_MIN` | `15` | Snapshot delay; set `0` on a real-time plan |

## Environment variables

See `RAILWAY_ENV_VARS.md` for the full list (Polygon, xAI, Supabase, FMP,
email transport, MCP auth). **Never paste actual keys into any file in this
repo.**

## Migrations

`migrations/*.sql` — apply in order against the Supabase project. The
`scan_snapshots` table is also auto-created on first write.

## Deployment

Railway: `railway.toml` (healthcheck `/health`) / `Procfile`
(`uvicorn server:app`). After changing variables, redeploy.
