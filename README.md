# Watchtower MCP Server

Lets Claude.ai chat with the [Watchtower](https://github.com/Ekonoski/watchtower) stock-finding platform.

## Tools exposed

| Tool | What it does |
|------|--------------|
| `master_screen_top` | Top stocks ranked by multi-signal master conviction score |
| `screen_detail` | Full per-screen breakdown for one ticker |
| `compare_tickers` | Side-by-side comparison of multiple tickers |
| `sector_summary` | Top stocks in a sector |
| `upcoming_earnings` | Earnings calendar with trailing surprise context |
| `recent_earnings_beats` | Stocks that just beat estimates |
| `recent_insider_activity` | Net insider buying by stock |
| `institutional_accumulation` | 13F top-10 holders increasing positions |
| `analyst_grade_changes` | Net analyst upgrades by ticker |
| `watchlist_list` | Show your watchlist |
| `watchlist_add` | Add a ticker to watchlist |
| `watchlist_remove` | Remove from watchlist |
| `watchlist_alerts` | Recent triggered alerts |
| `backtest_summary` | Past backtest run results |
| `backtest_top_picks` | Winners/losers from a specific run |
| `sql_query` | Free-form read-only SELECT against Watchtower tables |
| `db_schema` | List tables / columns |

## Deployment (Railway)

Required env vars: `MCP_AUTH_TOKEN`, `SUPABASE_DB_*`, `PORT` (set by Railway).

Start command: `python server.py`. Health: `GET /health`.

## Claude.ai connector registration

1. Get the Railway public URL (e.g., `https://watchtower-mcp.up.railway.app`)
2. In claude.ai → Settings → Connectors → Add Custom Connector
3. URL: `https://watchtower-mcp.up.railway.app/api`
4. When prompted, paste `MCP_AUTH_TOKEN`

OAuth 2.1 / PKCE handles the rest.
