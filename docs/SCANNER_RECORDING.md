# Background scanner recording

With WATCHTOWER_LIVE_TAPE=true the existing single stream starts during ASGI
startup, even with no browser connected. Deployment remains one process and
one Railway replica; do not create a competing stock socket.

`public.scanner_journal` stores the generated 5%/5-minute and 10%/10-minute
up/down alerts, including all reference context known at trigger time. These
are Watchtower rules, not a claim of exact Warrior HOD or five-pillar eligibility.
UI filtering, selected ticker and the 150-alert display limit do not affect storage.
There is no automatic order, email, browser control, or chart modification.

Record kinds: run_start, alert, connection, heartbeat (30 seconds), run_stop.
Each row has a run ID, stable event ID, Eastern session date, server observed_at,
database saved_at, and full payload. Alert market_at and received_at remain
separate. Up and down directions, thresholds, cooldown, warmup, float/RVOL
errors and freshness survive export. No historical reconstruction fills holes.

Writes run in a worker thread and retry with the same IDs after failure, without
blocking the market-event loop. The buffer is bounded at 20,000 records and
32 MiB of serialized payloads (Python object overhead is additional), batches
at 500; overflow increments an explicit loss count. At most one write runs at a
time. Database commits survive deployments; queued rows can be lost on a crash.
Long database outages can exceed the buffer. The UI exposes write errors,
pending counts, losses and last saved time via authenticated SSE. The recording
status endpoint is read-only and does not start the stream.

Authenticated endpoints:
- `/api/trading/recording`: stream and persistence status.
- `/api/trading/history?session=YYYY-MM-DD&offset=0`: up to 5,000 rows ordered by
  observed_at/id, explicit total and next_offset. Export after the session settles
  for stable pagination. The trading page's date picker fetches every page.

Compare Warrior's **expanded** historical alerts within matching families.
Do not compare a collapsed last-alert timestamp with Watchtower's first alert.
These are server trigger/receipt comparisons, never verified display latency.
Top-list membership is captured separately as described below. News delivery
and browser render history are not reconstructed from these alerts.

## Stock-selection history

The server samples the same ScannerFocus views as the UI at a target cadence of
one second, independently of database writes and browser requests. It records
leaders, fresh momentum, five-minute focus holds and volume lists across the
preferred ($3-$8), broader ($3-$20) and unrestricted bands. All keep the UI's
100-row cap, total eligible counts, omitted counts, missing day references and
warmup counts. This is the observed streaming universe, not proof that every
listed US stock had coverage.

`list_snapshot` stores full ranked membership every 30 seconds and at startup,
session/reconnect boundaries and sampling gaps. Rows are deduplicated by ticker
within each snapshot, preserving row timestamps, prices, changes, volume,
float/RVOL values and availability, and focus-hold details. Rank is the index
in each list's ticker array plus one. Rank-only changes are checkpointed, not
recorded tick by tick.

`list_membership` records entrants and departures between samples, with ranks
and row context. Arrivals are new within the observed scope or returning;
initial/reconnect rows are baseline, never proven first appearances. A sample
interval over 2.5 seconds is explicitly a gap and new rows say after_gap.
Membership shorter than the actual sampling interval can be missed. Expiry from
the 15-second freshness filter, rank displacement, band changes and eligibility
changes all remove rows; departure alone does not mean a trade failed. Failed
stocks and returning leaders remain in the historical records.

The combined export includes these records alongside alerts and coverage.
Compare against Warrior's history at matching dates/times and scanner families;
do not claim screen latency or exact Warrior rules from list observations.

## Continuation watchlist

The Continuations tab carries runners from the five most recent cached SPY
session dates strictly before today. A cached daily high at least 10% above the
immediately preceding session's close and close x volume >= $1m qualifies a
historical candidate. These are research defaults, not verified Warrior rules.
No price band gates the stored universe; UI bands and their 100-row caps apply
afterward. A failed close does not erase a qualifying earlier high.

From deployment onward, live +10%-day runners with price x fresh cumulative
volume >= $1m also create `continuation_candidate` records (first qualification,
then new five-percentage-point peak buckets). These are carried on later days;
today's candidates do not backdate themselves into yesterday's watchlist.
This covers future premarket-only spikes missed by daily-high data. Historical
premarket-only spikes and stocks absent from daily_prices cannot be recovered.
Ticker identity changes, splits and catalyst news are unverified, explicitly
tagged for manual review. Fresh news is not a requirement.

`continuation_universe` freezes the source dates, bar fields, source-row creation
time, prior runner episodes and loaded coverage counts. Refreshes are hourly and
on ET date rollover. Database failure is unavailable, not an empty universe;
retained rows stay visible but cannot claim current return signals.

Every second, `continuation_state` records changed volume/momentum flags:
- Momentum: current price <=15s old and observed one-minute change >= +3%.
- Volume: complete/fresh observed 1m >=10k shares, 5m >=25k shares, and 1m volume
  >=2 times the per-minute average of that same 5m window. This is LOCAL volume
  pace, not historical RVOL and not Warrior RVOL. Missing/warming/stale volume
  is null, never false/zero. Confirmation of a completed five-minute observation
  window can lag a fresh session start; the separate momentum flag can turn on
  sooner.
- Both: both conditions currently true; these are observations, not buy signals.

Quiet, cooling and failed names remain visible. Initial/reconnect state is a
baseline, not a proven crossing. Sampling gaps and original event/receipt times
are retained. Without today's trades, the displayed price is explicitly
historical/stale, not a live quote. Snapshots/membership exports include the
continuation family; detailed transition records include names beyond UI caps.

Coverage is unknown before run_start, after the last heartbeat, across unexpected
run boundaries, disconnects, missing heartbeats, storage losses, and warmup
(five/ten minutes after a fresh stream). Quiet/no-trade heartbeats show the worker
is running, not that every market event was received. Failure and loss counters
are cumulative per run. Unexpected exceptions or terminal authorization errors
render worker_stopped; do not score that interval as zero missed alerts.

The database table has RLS enabled and no anon/authenticated grants or policies.
Only the server's existing database role writes/reads it. Schema creation SQL is
kept in analysis/scanner_journal.py (SCHEMA) and applied during deployment.
