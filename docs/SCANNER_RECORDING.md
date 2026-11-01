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
blocking the market-event loop. The buffer is bounded at 20,000 records, batches
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
Top-list appearances, news delivery and browser render history are separate
datasets and are not reconstructed from these alerts.

Coverage is unknown before run_start, after the last heartbeat, across unexpected
run boundaries, disconnects, missing heartbeats, storage losses, and warmup
(five/ten minutes after a fresh stream). Quiet/no-trade heartbeats show the worker
is running, not that every market event was received. Failure and loss counters
are cumulative per run. Unexpected exceptions or terminal authorization errors
render worker_stopped; do not score that interval as zero missed alerts.

The database table has RLS enabled and no anon/authenticated grants or policies.
Only the server's existing database role writes/reads it. Schema creation SQL is
kept in analysis/scanner_journal.py (SCHEMA) and applied during deployment.
