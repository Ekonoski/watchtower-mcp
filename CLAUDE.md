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
