# Watchtower MCP — working notes

Read `README.md` for what the service is. This file is about not shipping
confident wrong answers, which is the failure mode that actually costs money
here.

## The rule: a summary must not drop what would change the conclusion

Every tool in this repo feeds a trading decision through a model that will
generally trust what it is handed. A summary that silently discards a
qualifying field does not produce a vaguer answer — it produces a confidently
wrong one, and nobody downstream can tell.

This has bitten us for real. On 2026-07-24, `_oscillator_block` flattened the
`signals` JSONB to `.keys()`, so `mf_curl {"dir": "down"}` rendered as the
bare word `mf_curl`. Printed beside a `bullish` label it read as agreement,
and a session was told a name in free-fall showed a "bounce signature." On the
4h the same flattening turned a 2-of-4 **bearish** divergence into the bare
word `divergence` next to `bullish`. The data was right in the database the
whole time.

So, when writing anything that renders a scan row:

- **Keep the direction.** `mf_curl` is not a signal; `mf_curl down` is. If a
  value carries `dir`, `zone`, `count`, or `volume_backed`, render it.
- **Show the confirmation layer.** The oscillator's own reading guide calls
  MACD the confirmation layer. A direction label without it is half a fact.
- **Score, not count.** Print confluence as `33/100`. `33 confluence` reads
  like a tally of agreeing indicators.
- **Stamp freshness per row, not per page.** Timeframes settle at different
  times: on 2026-07-25 the NOK 1h/4h rows were Jul 24 bars while the daily was
  still Jul 23 — one session stale, across a -6.5% day. A page-level "bars
  through" header hides that.
- **Never truncate silently.** No `[:3]` on a signal list. If you must cap,
  print what was dropped.
- **Warn when the headline contradicts its own internals.** `_osc_conflicts()`
  does this for the oscillator; `_gamma_block`'s decoration warning does it for
  gamma. Follow that pattern for any new headline number.
- **A failed lookup is not a neutral reading.** Error paths return
  placeholder values (`sentiment=neutral, score=0.0`). Render those as
  *unavailable*, never as data — see `format_buzz_for_display`.
- **An unordered `LIMIT 1` is a random row.** `_social_block` selected from
  a table holding months of history per ticker with no `ORDER BY` — a May
  snapshot could render as today's sentiment. Any `LIMIT` on a history table
  needs an explicit `ORDER BY <freshness> DESC`, and the render should stamp
  the row's own date so staleness is visible even if the query regresses.
- **Render the columns the table actually has.** The social section read
  `sentiment_label`/`summary` — keys that don't exist in `social_buzz` — so
  it silently never rendered. A section that can never fire is invisible in
  every test that doesn't assert its presence; when adding a section, write
  the test from a real row, not from the keys you assume.
- **Keep error text long enough to explain itself.** `str(e)[:60]` cut xAI's
  403 body at exactly the character where it names the cause (credits vs
  key). Log the whole exception; keep ≥300 chars in any surfaced summary.

## The morning brief carries the desk ledger

Every trading-day brief includes a **Desk ledger** section — the paper desk's
own record, stated every morning whether or not anything happened. The desk is
a live experiment (est. 2026-08-07) measuring its books against backtest
priors; an evaluation that only reports on eventful days is not an evaluation.

Content, per book (`gamma_iday`, `swing`):

- **Armed today**: spec count, plus skips with their reason (`skipped_binary`
  renders as the *decision* it is, with the binary named — it was the desk's
  best call of week one).
- **Fills yesterday**: ticker, entry vs trigger, and the R at risk.
- **Exits yesterday**: ticker, realized R, exit reason — **worst R first**.
  Losers lead; a ledger that buries them is marketing.
- **Open positions**: ticker, entry, stop, unrealized R, days held — each with
  its stop visible and graded on completed closes (no-wick rule).
- **Running record**: cumulative realized R and win/loss count since
  2026-08-07, per book. Below ~30 resolved trades, print the count beside any
  win rate so nobody mistakes anecdote for evidence.

Canonical queries (column names verified against live schema):

```sql
SELECT book, status, count(*) FROM paper_specs
WHERE trade_date = CURRENT_DATE GROUP BY book, status;

SELECT s.book, s.ticker, s.direction, t.entered_at, t.entry_px, s.stop,
       t.exited_at, t.exit_px, t.exit_reason, t.r_multiple
FROM paper_trades t JOIN paper_specs s ON s.id = t.spec_id
ORDER BY t.exited_at NULLS FIRST, t.r_multiple ASC;
```

Rendering doctrine, same spirit as the rest of this file:

- **Zero is data.** "0 fills · 151 armed · 4 skipped (NFP)" prints in full. A
  quiet day recorded is evidence; a quiet day omitted is a hole in the record.
- **The section renders every trading day or renders as *unavailable*** — if
  the paper tables are unreachable, say so. It must never silently disappear
  (`_social_block` was invisible for weeks because a section that can never
  fire fails no test).
- **Stamp the ledger's own freshness**: the max `created_at` it was read from,
  not the brief's timestamp.
- When a resolved trade's spec came from a scanned pattern, name the pattern
  and its backtest prior beside the result — the ledger's job is comparing
  live results to priors, not just counting money.
- **Structure shorts are retired from entries** (decided 2026-08-08 on the
  regime cut): 728 short episodes graded net-negative in EVERY regime — and
  WORSE in weak tape (29.5% win, n=129 SPY-below vs 40.6% SPY-above). No
  half-sizing a negative edge. Lower-high / bear-flag / breakdown detections
  serve as WARNINGS on held longs, not entries. Gamma wall-fades are a
  different mechanism and stand or fall on the replay harness's own grades.
- **The fill model is declared, symmetric, and phantom-proof** (2026-08-08
  audit: blind fills at the trigger created a phantom loss — ARW "filled" at
  220.87 on a day whose high was 209.60 — AND an asymmetric winner — TNDM
  booked at 18.16 it never touched from above). Swing fills: on the retest
  side, a limit fills on a touch at the trigger; a bar OPENING through the
  trigger means the level was lost — **dead-on-arrival cancelled if the open
  is already beyond the stop**, otherwise the spec enters RECLAIM mode and
  fills only on the first completed 15m bar CLOSING back through the trigger,
  at that bar's close — a wick through is not proof (the wick rule governs
  entries too); the premium over the trigger is the cost of confirmation.
  Every fill price is a price that printed, and R is computed from the
  ACTUAL entry.
  One rule for winners and losers alike — any convention change renders in
  the ledger the day it ships.
- **The cost of confirmation is measured, not argued** (decided 2026-08-08:
  the swing book keeps resting-limit fills at the trigger). Every touch fill
  also records its confirmation shadow — the completed-15m-close entry a
  close-through desk would have paid (`confirm_px`, `confirmed`) or the
  trade it never took (`no_confirm`); reclaim and gamma entries are already
  closes (`n/a`). The ledger prints the running actual-vs-shadow comparison
  per book — entry premium paid vs losers skipped, with counts beside it
  (small-n rule applies). `unresolved` is a data hole and renders as one,
  never as a zero. The wick rule is untouched: stops, exits, and reclaims
  still decide on completed closes; a touch filling the resting limit is the
  order type's execution fact, and the shadow exists to price whether proof
  should be demanded there too.

## Numbers on one line must reconcile with each other

The brief's price line used a vendor `todaysChangePerc` next to a price and a
row of returns computed from our own daily closes. When the vendor's reference
close disagreed with ours it printed `-7.6% today` beside `$9.10` on a day
that was actually `-6.5%`. Derive from one source, and name the reference:
`-6.5% vs 2026-07-23 close $9.73` is auditable at a glance; `-6.5% today` is
not.

## House doctrine ships with the server

`_DOCTRINE` in `server.py` is sent to every MCP client as server instructions,
so any model on any device inherits the house rules without re-learning them.
If a reading rule is worth enforcing, it belongs there, not only here — this
file only loads when someone is working in this repo.

## Tests

No framework is wired up; `tests/` runs standalone.

```
python3 tests/test_brief_oscillator.py    # or: pytest tests/
```

`tests/test_brief_oscillator.py` pins both bugs above against real NOK scan
rows. Add cases here when a rendering bug reaches a session — a bug that
shipped a wrong answer once is worth a permanent test.
