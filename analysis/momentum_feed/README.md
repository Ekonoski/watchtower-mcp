# TrendSpider portfolio feed

User-authorized forward continuation of the researched `retention_buffer` stock
portfolio. **Model decisions, not brokerage executions or options trades.**

The frozen engine is extracted from the audited Oct 31 research engine. Bootstrap
parity: all 582 entries, 572 completed positions (571 market exits and one merger
settlement), every reserve leg, dividend, and equity observation match exactly
when terminal liquidation is enabled. Forward mode suppresses only that artificial
terminal liquidation and returns the ten ongoing stock positions plus QQQ.

Rules: frozen December 2017 40-stock cohort, 63/126-session blended momentum above
both SPY/QQQ, close above SMA200, $20M daily dollar-volume average, top10 entries,
top15 retention, four per sector, ten direct holdings, 0.75% initial stop-risk
budget, 10% initial notional cap, 3 Wilder-ATR fixed close stop, next-open exits,
Mon/Thu session-block rotation, skip opening gaps above prior close + 1 ATR.
QQQ holds residual capital. Costs remain 15 bps per side; no leverage. This is a
research candidate, not established durable outperformance or a new validation.

## Operation

The existing Railway scheduler checks every two minutes. No emails, Discord,
broker calls, or changes to paper desk books. State, captured prices, and dated
payloads live in the private `wt_momentum` schema. RLS is enabled; anon and
authenticated have no schema/table access. The chart endpoint accepts only the
dedicated feed token's SHA256 hash, never MCP/database/provider credentials.

The Oct 30 bootstrap is explicitly historical. At 09:31–09:45 ET the service
records each active cohort member's 09:30 regular-session one-minute opening
print. Only then does it record that day's opening model decisions. These prices
are **observed model reference fills**, not achievable quotes guaranteed to a
user seeing the arrow later. Prior-close plans give the user advance notice.
At/after 16:20 ET it records completed daily OHLCV and produces the next session's
conditional plans, including holidays. Early-close sessions also settle at 16:20.
No intraday stop or wick exit is invented. Stop exits are planned only after a
completed close breaches the fixed stop, for the following session's open.

Opening observations are immutable. Later daily-bar opening-price revisions are
retained as raw evidence but never rewrite model fills. A missed opening/session
blocks continuation for manual reconciliation, rather than labeling historical
reconstruction as live tape. Retries serialize via a PostgreSQL advisory lock and
commit observations, snapshot, and current state together.

Splits after cutoff normalize forward bars/dividends into the fixed seed basis;
current stops, units, and event prices translate back to their dated actual basis.
FB/META, PCLN/BKNG and SQ/XYZ retain historical identities. AET/AABA remain retired.
Cash dividends use prior-close entitlement and pay-date cash availability.
New ticker changes, mergers/spinoffs or non-cash distributions require explicit
verified handling; missing bars/distribution fields fail closed. The feed does
not claim automatic coverage of every possible corporate action.

`settings.enabled=false` disables the feed. Rotate only its token hash to revoke
chart access. Never give TrendSpider the MCP, Supabase or Massive credentials.
Settings and historical observations are private even though the HTTPS route is
reachable. The GET endpoint has no write or order-execution operations.

## Chart

Install `trendspider.js` as a private Daily/candles indicator, then populate the
dedicated feed key. Watchtower runs independently of an open chart. TrendSpider
fetches on indicator evaluation, with a 30-second HTTP cache; the indicator does
not promise push delivery while closed. Refresh the chart to verify its timestamp
before acting. Server heartbeat expires after seven minutes; response expires in
three. Stale/error responses suppress new plans and alert signals.

BUY/SELL history labels precede activation; later labels say model. Status states
HOLD/FLAT, stop, next-session conditional plan, portfolio plans and checked time.
Conditional BUY candidates are a priority list: earlier gaps, cash and sector
limits can determine whether later candidates fill. No alert is automatically
armed. Registered signals are available for a separately configured alert.

## Validation

`tests/test_momentum_feed.py` checks bootstrap continuity, the retained holding
that blocks JNJ, completed-close causality, split display basis, missing-cohort and
missed-session refusal, stale-state suppression, holidays and invalid-bar refusal.
The research-side `build_live_seed.py` proves exact audited-path parity before
exporting `seed.json.gz` (immutable normalized features, distributions and merger).
The installed seed is checked against provider cutoff closes at first activation.
