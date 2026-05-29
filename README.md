# Watchtower MCP Server

Lets Claude.ai chat with the [Watchtower](https://github.com/Ekonoski/watchtower) stock-finding platform.

## Tools exposed

**Primary screens (start here):**

| Tool | What it does |
|------|--------------|
| `reversal_candidates` | Beaten-down quality stocks turning up — 8/13 EMA, RSI recovery + divergence, MACD, volume, 50 EMA composite |
| `insider_burst_plus_tech` | Backtest winner (+13.9pp vs SPY): ≥3 net insider buys + ≥10% off high + RSI rising or MACD positive |

**Per-ticker + comparison:**

| Tool | What it does |
|------|--------------|
| `screen_detail` | Full per-screen breakdown for one ticker |
| `compare_tickers` | Side-by-side comparison of multiple tickers |
| `sector_summary` | Top stocks in a sector |

**Catalyst + activity feeds:**

| Tool | What it does |
|------|--------------|
| `upcoming_earnings` | Earnings calendar with trailing surprise context |
| `recent_earnings_beats` | Stocks that just beat estimates |
| `recent_insider_activity` | Net insider buying by stock |
| `institutional_accumulation` | 13F top-10 holders increasing positions |
| `analyst_grade_changes` | Net analyst upgrades by ticker |
| `social_buzz_top` | Reddit + WSB mention surge (24h) — situational awareness |

**Watchlist:**

| Tool | What it does |
|------|--------------|
| `watchlist_list` | Show your watchlist |
| `watchlist_add` | Add a ticker to watchlist |
| `watchlist_remove` | Remove from watchlist |
| `watchlist_alerts` | Recent triggered alerts |

**Broad context + research:**

| Tool | What it does |
|------|--------------|
| `master_screen_top` | 9-signal fundamental composite (no technicals — use the primary screens for setups) |
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
