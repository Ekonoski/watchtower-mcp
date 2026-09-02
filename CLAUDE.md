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
  ACTUAL entry. **Only regular-session bars decide** (2026-08-08):
  premarket moves are low-volume fakeouts — bars are persisted whenever
  seen (`paper_spec_bars`), but fills, stops, and shadows count only bars
  from 9:30 ET completing by 16:00. The tape that forced it: MOS's
  premarket dip touched a limit the regular session never confirmed.
  **Reconstruction is not tape**: any backward-looking price must come
  from recorded bars, never refetched or inferred history (the fabricated
  TNDM "18.60" reached a card labeled *real* before chart verification
  killed it). **Geometry must survive the entry** (2026-08-08): the ratio
  the spec qualified on is re-checked at the actual fill price on reclaim
  entries — a violent premium can collapse 2.1:1 to 0.79:1 (TNDM) —
  and collapsed geometry cancels (`reclaim_geometry`), the refusal
  gradeable from recorded bars. The re-check demands the class's NATIVE
  ratio (`native_geometry_ratio`, one source with admission), not a flat
  1.5 — learned 2026-08-12 when ATRC's 1.5-cent reclaim premium on a 1:1
  measured-move class was refused at "1.00:1 vs 1.5", a bar no neckline
  class can ever clear; the 2026-08-11 admission fix had left the
  entry-side gate class-blind.
  One rule for winners and losers alike — any convention change renders in
  the ledger the day it ships. **The daily close means the TRUE close**
  (2026-08-15, the AGMB 0.9-cent case): the loop's window ends 15:58, so
  its "daily close" was the 15:30–15:45 bar — AGMB closed that bar 0.9
  cents above its stop, printed the true close 12 cents through it, and
  no exit fired. A 16:20 settling pass (`run_swing_close_settle`) now
  decides open swing positions on the recorded final RTH bar (persisted
  by the 16:07 closing-bar pass — never refetched; a missing bar is a
  logged hole, not a decision). Eric ruled the convention retroactive to
  its discovery: AGMB settled at 13.03 for −1.07R, the book's first
  swing exit. `tests/test_swing_settle.py` pins the bars and, by
  signature, the decision's inability to fetch.
- **The swing book trades positive-prior classes only** (2026-08-08):
  `SWING_CLASSES` in `paper_trader.py` is the explicit (pattern, timeframe)
  allowlist with each class's backtest prior beside it. No flat
  pattern×timeframe lists — ema_bounce is the best weekly class (+0.98R,
  n=205) and the worst daily one (−0.37R, n=162), so only a joint gate can
  hold the line. The daily neckline classes (higher_low −0.06R, double_bottom
  −0.19R) ride as a declared experiment: their priors were graded on
  breakout-close entries, the desk buys the retest at the trigger, and they
  retire like shorts did if still negative after ~30 resolved live trades.
- **The ledger grades the signal; the expression layer waits for the data**
  (decided 2026-08-10, the day of the desk's first resolved trade — SPY
  wall-fade, +1.03R). Every book records entries/exits in UNDERLYING price
  and R, instrument-agnostic on purpose: grading the edge in option P&L
  would smear theta/IV/strike noise into the question "do the levels
  work?". Going live adds a SEPARATE options-expression layer — for the
  gamma book, short-dated puts vs a short call spread above the wall,
  chosen from our own IV-rank history (the 5:35 IV snapshot job is
  accumulating exactly this); `watchtower_option_ticket` builds the
  ticket. The expression is graded against the shares-equivalent as its
  own scoreboard, the same way touch fills carry a confirmation shadow —
  the edge and the expression each answer for themselves. **Gate (Eric):
  build the conversion only once the paper record shows profit with
  roughly two months of live data (~30+ resolved trades per the small-n
  rule) — not before.** Until then the desk stays paper and the ledger
  stays in underlying R.
- **Assert admission, not just detection** (2026-08-11, the geometry-gate
  lockout): inverse_hs and double_bottom sat on the SWING_CLASSES allowlist
  scoring in the 80s while the flat 1.5:1 geometry gate — impossible for a
  measured-move target, whose R:R is 1.0 by construction — silently made
  them unarmable. Two days of all-higher_low books read as "what the
  scanner found" until Eric asked "just higher lows?". Every unit was
  correct; the integration property was asserted nowhere. The rules that
  came out of it: every allowlisted class carries a declared NATIVE
  geometry that `tests/test_class_admission.py` proves can pass its gates
  (a class that can never fire fails CI, not a human's attention); the
  writer logs per-class in-band→eligible admissions every morning and
  WARNs on candidates-with-zero-eligible; and the ledger's "armed today"
  states the class mix, so a monoculture renders as a question. Same
  family as `_social_block`: zero fills from armed specs is the market
  declining — zero specs ever armed is the system declining, and only the
  first kind was being watched.
- **A flat score bar is a class gate in disguise, and a flat top-N is a
  class cap in disguise** (2026-08-16, Eric: "we need to get these active
  so that we have real data… right now we are not working with a real
  system"). Week one's book was 100% higher_low/neckline not because the
  scanner found nothing else but because the writer's flat `score >= 70`
  sat above cup_handle's best live row (67.9) and falling_wedge's (61.0)
  — detector quality scales differ, so one bar across classes is the flat
  1.5:1 geometry mistake wearing a different number — and the flat top-15
  by score let ~260 higher_low/neckline candidates flood every slot
  ema_bounce (24 candidates, all passing geometry) might have taken. The
  allowlisted experiments simply weren't running, and only the ledger's
  class-mix line could have said so. Now: every class present gets ONE
  guaranteed slot (its best candidate, admitted at `SWING_SCORE_FLOOR`
  55); the remaining slots keep the old open-competition bar
  (`SWING_SCORE_OPEN` 70); the writer logs the floor grants and the armed
  class mix every morning. A floor is one slot, never a flood —
  `tests/test_paper_intraday.py` pins that five queued wedges still yield
  exactly one wedge. Same day, the deeper cousin: **bull_flag could never
  fire at all** — the detector hardcoded `forming` AND sought its pole
  among all window bars, so the breakout bar became its own pole and the
  detection dissolved at the exact moment it became tradable (443 live
  flags, zero ever breakout/retest — the `_social_block` family, in a
  detector). Flags now run the same `_status()` lifecycle as every other
  class, the pole must be the high of its own run, the flag region ends
  at the first CLOSE through the pole (wick rule), and when one tape
  reads both as a broken pole in throwback and a marginally higher
  "fresh flag," the ACTIONABLE reading wins. `tests/test_flag_lifecycle.py`
  pins forming → breakout → retest, the wick refusal, and the spent drop.
  Same day, the full-catalog audit (Eric: "All patterns not just bull
  flags") — a per-class × per-timeframe status census of the live board
  is the cheap test for this whole disease family, and it convicted two
  more: **range_breakout/range_breakdown** could only say breakout
  (still beyond the edge) or forming (inside the box), so the throwback
  retest — the entry the desk buys — was unreachable by construction on
  every timeframe; and **wma_touch**'s 40-week qualifier walk started at
  the newest completed week, so the touch week itself (closing at/below
  the line) zeroed the run and the event erased its own detection —
  'retest' existed in code and had never once existed on the board. Up
  to 3 trailing touch weeks now get grace (each above the −3% failure
  line; grace never excuses a mid-run break — the qualification
  precedes the touch, per the study's own definition).
  `tests/test_range_wma_lifecycle.py` pins both. Cleared by the same
  census: necklines, higher_low/lower_high, triangles, wedges, cup,
  ema classes all show all three states; diamonds/desc_triangle route
  through `_status()` and are small-n, not structural. The audit rule
  going forward: a class's status census belongs beside its detection
  count — a pattern that detects hundreds and never reaches its
  actionable state is a `_social_block`, not a quiet market.

- **Sector rotation: measured, and the aggregate thesis didn't survive
  contact** (2026-08-22, Eric: "you can pick a great stock but be in
  the wrong sector while money rotation is flowing out"). Built per
  doctrine — breadth-style sector RS cache (`sector_rs_daily`, median
  stock vs market median, one definition in `analysis/sector_rs.py`),
  a 24-month study over 24,113 graded daily bullish episodes
  (`sector_study`, marker `sector_study_v1`), and a measurement-only
  `sector_state` tag on swing specs (osc_state pattern; arming blind
  by pinned signature; gamma specs untagged). The read, outliers
  capped at 10R: FLAT in aggregate — outflow-sector breakouts (rank
  9-11) graded 61.3%/−0.02R vs inflow 60.5%/−0.07R vs a −0.07R pool;
  no monotone RS-quintile gradient. The one live cell: sector washed
  out on the month but TURNING UP on the week — 63.0% win, +0.073R
  (n=2,261), the only positive state, the sector-level echo of the
  washout-reversal read; chronic-outflow-still-falling was merely
  average, and NEUTRAL sectors were worst (58.4%, −0.113, n=10,383).
  Likely reason the poison didn't show: a breakout-retest pattern
  already demands the stock carved its own base — the structure
  embodies the turn before the sector does. Per-pattern cuts flip
  sign wildly (falling_wedge loves inflow +1.14 vs −0.77; ema_bounce
  daily the reverse) — one more proof a flat sector rule would be the
  flat-score-bar mistake again. Verdict: NO GATE EARNED. The tag
  stamps every swing spec from Monday 2026-08-24 and grades on the
  desk's own resolved trades; the washout-turn cell is a candidate
  SELECTOR to re-examine at sample size, never a chase signal, and
  any promotion is asymmetric like the cipher's. Caveats where the
  numbers surface: breakout-close entries (conditioning read), RS is
  market-relative, and the desk buys retests.

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

- **cipher_reversal is a named screen, not a gate** (2026-08-14, the LNG
  miss): asked for charts matching the NFLX-3D state — money flow deep in
  the red and curving up, waves crossing up from the low band, RSI turning
  — the mf_round screen surfaced LNG's 4h (MF **+1.9**, %R −3), because
  the arc SHAPE carries no requirement on the LEVEL it turns from. The
  composite (`cipher_reversal` in `evaluate_signals`, screener setup of
  the same name) makes the level a hard leg: mf trough ≤ −8 and still
  red, curving up, wave cross-up ≤ 8 bars old from the lower half, RSI
  turning; MACD higher-low tags `full_stack`, never required. Graded at
  the 320k v6 episodes: daily bullish breakouts in the core state pay
  **+0.105R (n=6,505, 63.9% 1R) against a −0.012R daily baseline**, the
  gradient in wash depth is monotone, and the shape-only cohort (same
  curve, mf > 0) grades **−0.020R (n=8,578)** — the level IS the signal.
  On the WEEKLY the state underperforms the +0.357R weekly baseline
  (+0.167R, n=1,473): washouts are a daily/intraday reversal read; the
  weekly selector remains mid-band strength (the cipher-tag finding).
  Caveats stated where the numbers surface: graded on breakout-close
  entries at pattern episodes, and the record can't express the RSI-turn
  or cross-freshness legs, so the live definition is stricter than the
  graded core. The composite is confluence-blind by test
  (`tests/test_cipher_reversal.py`) and earns its own 7/30/90-day
  forward returns via alert-performance. Same-evening calibration
  (Eric, reviewing the first live list: "these charts do not match")
  added two legs the first cut missed: **location** — the wave trough
  must sit in the lower band (≤ −40 within 10 bars; ALG's 1h "wash" was
  a −20/−33 mid-range wobble in chop) — and **timing** — RSI ≤ 60 at
  fire, because recovered isn't turning (in the deep-flow wash zone,
  RSI ≤ 60 grades +0.146R n=1,093; RSI > 60 grades **−0.194R** n=78).
  The same review caught two screen-surface sins: intraday freshness
  was 4 days (ADT's Tuesday 1h bar rendered as Friday's state — now
  1h = same day, 4h = 2 days) and rows carried no bar stamp (now
  per-row, the stamp-freshness-per-row rule applied to the screen).
  Second calibration pass, same evening (AGO vs UNH): the **green-RSI
  leg** — AGO carried the full stack but its StochRSI pair had run to
  84/64 with the cross 7 bars old (turn SPENT, no green on the panel);
  UNH sat at 37/18 curling with the cross that bar. So stoch_d ≤ 50
  with k ≥ d, and cross freshness tightened 8 → 4 bars (the NFLX-3D
  archetype fired at 3; AGO's 7 was a chase). Stoch isn't in the
  episode record — this leg grades live via alert-performance, stated
  wherever the numbers surface. And Eric killed the freshness-gate
  excuse correctly: the data is real-time, the CADENCE was the gap —
  the 4h/1h oscillator scan now refreshes hourly through the session
  (10:05–15:05 plus the 12:45 chain), so stored intraday rows track
  the tape, not the morning. Third pass, same evening: the **%R leg**
  (Eric: "add a curving up williams %R from the bottom") — %R(28)
  pinned ≤ −80 within 10 bars and rising at fire; like the stoch leg
  it isn't in the episode record, so it grades live. And the daily
  side of "our data is realtime": `analysis/close_sync.py` upserts
  the session's grouped-daily Polygon bars at 4:35 PM and re-stamps
  the fleet, so daily/weekly rows carry TODAY at evening review
  instead of waiting for the 10 PM price-cron ("why thursdays bars?"
  — because the scan predated the ingest, and Friday's close would
  otherwise not exist on a screen until Monday). The nightly
  price-cron (`polygon_price_daily`, per `ingestion_log` — not FMP as
  first written here) stays the settling authority (10:45/11:30 PM
  re-stamp), and a 0-row sync skips the re-stamp loudly rather than
  dressing up a stale table. Fourth pass (COLM, then GOOS — "neither
  one of them have deep red money flow"): **still-red-NOW** — the
  trough leg alone let flows that had recovered to a sliver (−10.4 →
  −3.4, −10.0 → −1.2) fire while rendering neutral on the panel, so
  the flow must also be ≤ −4 at the fire bar. Stated honestly: the
  record is equivocal inside (−8, 0) (−8..−4 grades +0.054, −4..0
  +0.085, both under the ≤ −8 core's +0.105), so −4 is LOOK
  calibration graded live, like the stoch/%R legs. And "rounded, not
  jagged": a confirmed mf_round arc is the archetype — it ranks first
  in the screen and the payload tags `rounded`; a jagged three-bar
  rise still qualifies but says so.

- **The %R higher-low family: one reversal, three ages** (2026-08-15
  weekend calibration — CHWY the archetype, NI/MARA the refusals, SNAP
  the confirmation look). `pctr_hl` = the earliest whisper: two
  confirmed Williams %R(28) floor troughs (≤ −70) rising, the tape no
  longer printing 30-bar closing lows, still pre-breakout, %R ≤ −45.
  A saturated-floor pair (tiny lift, second trough still ≤ −88 — a
  pair pinned at −99 is the indicator clamped at its bound) fires
  TAGGED `shallow` and ranks LAST, never skipped — Eric's correction
  the same day: "sometimes those run like they did with CHWY"; the
  flavors grade separately via forward returns, and the NI/MARA
  knife-guard is the stabilized-tape leg, which stays hard.
  `base_turn` = the same structure with everything confirming: MACD
  hist green while the line is still under water, waves crossed up in
  (−50, +15), RSI 40–60, flow ≥ −10, price above its 8-bar average.
  With `cipher_reversal` between them the lifecycle reads pctr_hl →
  cipher_reversal → base_turn; earliest = most room and most risk. At
  the episodes, %R floor troughs of either flavor front-run 1R at
  64–66% vs a 57% baseline, but expectancy at breakout entries favors
  the fresh flush (+0.26R n=686) over the higher low (+0.03R n=1,430)
  — stated where the numbers surface; the live claim ("higher lows
  lead big moves") grades via alert-performance forward returns. Both
  are confluence-blind by test (`tests/test_pctr_hl_family.py`). And
  the MNDY lesson rides with them: every non-entry-grade screen row
  now carries its best live chart pattern with **⚠ on bearish
  structures** — MNDY topped the base_turn list while sitting on a
  freshly REJECTED breakout (Friday −7.2% off the 94.40 trigger), and
  a bullish panel at a rejected trigger must say so.

- **The BW-3D archetype experiment: built, graded, and fully reverted in
  one evening** (2026-08-16). Eric asked for "similar charts on the 3
  day mimicking all of the bullish indications" off the BW 3D chart.
  Two composites shipped and died on his chart checks — bull_embed (the
  embedded cruise: "No no no, the red into the green…") and
  red_to_green (the flip: "this absolutely is not it. Remove this from
  our system") — and the scanned '3d' timeframe they rode on was
  removed at his follow-up ("Remove that part from our build"). Nothing
  of the experiment remains in the scan, screens, UI, or perf pipeline;
  the on-demand 2d/3d single-ticker chat reads are exactly as they were
  before. What the evening left behind is knowledge, kept here: (1) the
  episode grades, outliers CAPPED at 10R — the daily just-green
  money-flow flip is a TRAP at breakout entries (−0.398R, median −1.00,
  n=8,594), the full-embed core underperformed baseline on both graded
  timeframes, the weekly flip beat baseline only modestly (+0.158R vs
  +0.117R); (2) one 2,260R outlier print inflated a raw weekly average
  9× before capping — outlier hygiene belongs in every grade; (3) an
  end-anchored k-day resample repaints every session and must never
  feed stored rows, and test fixtures built on DatetimeIndex miss that
  the fleet fetch indexes with raw date objects (a per-ticker except
  swallowed exactly that as zero rows on a "successful" scan); (4)
  verify the TICKER before verdicting a chart — the removal call was
  made on TradingView's ENR (Siemens Energy AG) while the system's ENR
  is Energizer Holdings (stated for the record; the removal stands).
  The standing rule, hardened: chart-look composites WAIT for the
  labeled exemplar set (the 2026-08-15 plan) — the eye is not specified
  by adjectives, and two same-evening attempts proved it.

- **Numbers at a bar are not the picture; paths are** (2026-08-15, the
  CEG/SNAP lesson): a nine-component snapshot fingerprint found CEG as
  SNAP's weekly twin — true to a tenth of a point at the bar and
  parallel across twelve weeks — yet the charts READ differently,
  because the eye matches months of SHAPE: mound count, whether the
  second mound is shallower, the staircase. `watchtower_match_chart`
  (analysis/shape_match.py) therefore matches TRAJECTORIES — each
  component's path over the lookback in fixed component units, with
  the wave-mound structure required to agree before a numeric twin can
  rank — two-stage (loose snapshot pre-filter, engine paths on the
  pool only), daily/weekly, holes reported as holes.
  `tests/test_shape_match.py` pins the core, including the
  state-twin-with-opposite-path case.

- **A long armed against a live bearish structure says so** (2026-08-13,
  the CIFR case): the writer's bullish-only candidate query armed a daily
  inverse_hs while the same scanner held a weekly hs_top (forming) and a
  daily lower_high (at retest) on the same ticker — and nothing said so.
  `bearish_conflicts` now stamps `⚠ bearish structure live: …` into the
  spec's rationale (the ledger carries it) and the morning log WARNs per
  ticker. Measurement only, never a gate — shorts are retired, warnings
  are warnings, and `tests/test_bearish_warning.py` pins the CIFR rows
  plus, by signature, the function's inability to gate. Related queued
  work: the CIFR detection itself (head Mar 31, right shoulder Aug 10,
  price 50% through the "neckline" mid-pattern) exposed a candidate
  detector constraint — disqualify a neckline pattern when price CLOSED
  through the neckline between head and far shoulder — which changes the
  class definition and therefore waits on a full re-grade before adoption.

- **The cipher rides as a tag, not a gate** (decided 2026-08-11, the night
  the cipher-at-episodes study read out — 320,144 v6 episodes through the
  LIVE compute_oscillator path). The finding: within the weekly RSI-45-60
  band, mf-slope-up + MACD-hist-positive + wt2-not-overbought separates
  +0.69R (n=9,264) from +0.10R (n=4,175); on the daily the same stack is
  only a veto (wt2>=53 at breakout = -0.18R, n=19,756) and reopens
  nothing. So every swing spec now carries `osc_state` — components at
  the timeframe's last completed bar, stamped at write time AFTER
  curation so the tag cannot influence arming even accidentally (a
  tiebreaker is a gate in disguise). The prior was graded on
  breakout-close entries and this desk buys the retest, so the tag is
  measured like the confirmation shadow: judged on ~30 resolved weekly
  live trades, then promoted asymmetrically (weekly selector, daily
  veto) or dropped. Two hard rules regardless of outcome: the blended
  0-100 confluence score NEVER gates anything (it sign-flips across
  timeframes — daily 40-58 bucket -0.64R, weekly +0.97R; components
  carry the signal, the composite is decoration); and a missing tag is
  `cipher_ok: null` plus a reason, rendered as *unavailable*, never as
  "cipher said no". `tests/test_cipher_tag.py` pins the contract, the
  hole-handling, and — by signature — the arming pipeline's blindness
  to the tag.

- **The binary-day skip carries its own shadow** (decided 2026-08-12, the
  CPI post-mortem — Eric watched the 8:30 print resolve by mid-morning and
  asked what the skip had cost; the honest answer had to be graded off
  15-minute gex snapshots because the bar watcher never subscribes to
  skipped specs). Whether the full-day binary skip over-pays is measured,
  not argued: every `skipped_binary` spec shadow-re-arms at 10:30 ET if the
  recorded 10:30 board (freshest `gex_intraday` row ≤10:30, same ≤25-min
  staleness bar as the live armer) still shows its level — matched through
  `build_gamma_specs` on live code (regime and every arming gate re-applied)
  as same setup family + trigger within 0.25%, the LEVEL and not the
  quantized name (the flip's cent-wobble is one level; a 775→780 wall walk
  is not). Re-armed shadows grade by the live gamma rules from recorded
  bars — skipped tickers' bars now persist to `paper_spec_bars` — wick rule
  on entries and stops, 14:30 no-new clock, eod_flat on the bar the 15:55
  pass reads, R from the actual shadow entry. The shadow never arms, fills,
  or cancels anything live (`tests/test_binary_shadow.py` pins this by
  signature, plus 2026-08-12's boards as the permanent fixture). On a
  binary day the ledger prints, beside the skips: shadow re-arms, shadow
  fills, and shadow R — `rearmed` NULL (board unavailable) and an exit-less
  entry (record cut mid-trade) render as data holes, never as zeros — plus
  the running shadow record with counts (small-n rule). "4 skipped (CPI) ·
  4 shadow-rearmed · 0 shadow fills · skip cost 0R" is the expected quiet
  row, and it is evidence. Promotion gate: ~30 shadow-resolved specs, then
  mid-morning re-arming goes live or the full-day skip stands vindicated.
  Canonical query:

  ```sql
  SELECT s.ticker, s.setup, r.rearmed, r.reason, r.entered_at, r.entry_px,
         r.exit_reason, r.r_multiple
  FROM paper_shadow_rearm r JOIN paper_specs s ON s.id = r.spec_id
  WHERE s.trade_date = CURRENT_DATE;
  ```

- **The gamma board's Imbalances read from the record, not the engine**
  (2026-08-13: the board shipped its FVG section as a declared hole because
  zones were only ever computed per-request in the dashboard). The 7:35
  sweep persists displacement-quality daily zones — gamma venues + active
  watchlist + every open paper position — into `fvg_runs`/`fvg_zones` from
  recorded `daily_prices` bars. A run row per ticker per sweep makes
  absence unambiguous: `n_zones = 0` is a recorded quiet read, a missing
  run is a hole (the `_social_block` family). `watchtower_fvg` serves the
  same read over MCP. Canonical query:

  ```sql
  SELECT r.ticker, r.bars_through, r.n_zones, z.side, z.status,
         z.top, z.bottom, z.age_bars, z.formed, z.inverted_on
  FROM (SELECT DISTINCT ON (ticker) * FROM fvg_runs
        ORDER BY ticker, computed_at DESC) r
  LEFT JOIN fvg_zones z ON z.run_id = r.id
  ORDER BY r.ticker, z.age_bars;
  ```

- **The walls move intraday, and the phone hears about it** (2026-08-18,
  Eric: "no, they change throughout the day. we have proven that" — he
  was right and the docstrings were wrong: OI is overnight-fixed, but
  re-pricing migrates the max-gamma strike and walks the flip; the
  recorded day-paths show it, and the first live alert caught QQQ's
  CW 730→700 / flip 723.83→721.67 at 2:15 PM). TradingView cannot
  ingest data (Pine sandbox), so the slot-update loop is Watchtower →
  Discord → the Tape Bot inputs: `alerts/discord_notify.py` (webhook
  pipe, configured-off is a clean no-op, at-most-once via
  `discord_notify_log` claims, failed posts visible as
  delivered=false), `alerts/gamma_drift.py` (baseline = the marks Eric
  holds, seeded 9:20 from the morning board before the intraday upsert
  overwrites gex_levels; material = wall on a different strike / flip
  ≥0.30% of spot / regime change; rate limit 40 min & 6/day per
  ticker; EVERY evaluation logged sent-or-suppressed-with-reason so
  `gamma_drift_alerts` measures how much the board actually walks),
  and `alerts/desk_events.py` (fills/exits/settle verdicts to #desk,
  reading the record only, worst R first, today's-events-only launch
  guard). The mega-caps (META/MSFT/AMZN/TSLA/GOOGL/AAPL/NVDA —
  `DRIFT_TICKERS`) ride the 15-minute re-price beside the indexes
  (2026-08-19: NVDA traded through its put wall while its freshest
  board was the 7:30 sweep); the rest of the single-name universe
  stays nightly + on-demand. A put wall ABOVE spot / call wall BELOW
  spot inverts the walls' meaning (stranded protection overhead;
  positive-gamma stabilizer underneath) — the doctrine read, now live
  on alerts. `tests/test_discord_alerts.py` pins thresholds,
  rate-limit reasons, slot-value formatting, configured-off, and the
  mega-cap set membership.

- **The defended-entry shadow: the eye's retest discipline, measured**
  (2026-08-21, Eric: wait for a lower-timeframe bounce with volume
  showing buyers DEFENDING the level — contracting red volume into the
  touch, a green uptick off it, one or two bars, RELATIVE to the
  pullback, never a spike requirement because spikes are late). The
  live book stays untouched (his own framing: human interference is
  the failing factor; this is an ADDITIONAL test, not a change). The
  existing confirm-shadow record already leans his way: resolved
  no-confirm touches −1.17R (n=4) vs confirmed −0.36R (n=3), and the
  two best positions ever held (CTNM +4.5R, ASTE +1.5R) were
  close-confirmed reclaim entries. Build: `paper_spec_bars.volume`
  (nullable — legacy rows are holes, shadows on them render
  'unavailable'), `analysis/defense_shadow.py` (pure `find_defense`,
  two variants recorded side by side — v1 one confirming bar over the
  pullback's red average, v2 two rising green bars — so the data picks
  the eye's definition; outcomes defended / knife_skipped / missed /
  no_defense; wick rule holds — only a CLOSE through the stop is a
  knife; the shadow rides the live trade's own exit, shadow_r beside
  live_r), and `analysis/defense_study.py` — the SAME detector graded
  at historical retest episodes (pattern_backtest.retest_bar + Polygon
  15m history; research backtests are legitimate — reconstruction-is-
  not-tape governs LIVE grading only), 1,200-episode sample, cipher-
  study-style boot seeder with resume + completion marker
  (`defense_study_v1`). Promotion gate ~30 resolved live comparisons,
  with the study prior stated beside them. tests/test_defense_shadow.py
  pins the defended/knife/missed/hole cases and, by signature, the
  module's inability to touch paper_trades/paper_specs.
  The retro read (2026-08-22, Eric: "see if any of our previously
  entered trades would fit our criteria"): a one-shot research job
  (`analysis/defense_retro.py`, marker `defense_retro_v1`) graded all
  45 pre-Monday touch fills from Polygon 15m history — verdicts in
  their own `defense_retro` table so reconstruction can never
  masquerade as the live record. Of 6 gradeable resolved trades the
  live book took −4.66R, the shadow −0.10R: four of five losers (CAE
  −1.51R the worst) never printed defense on entry day, AGMB bounced
  on sub-baseline volume (the filter called it fake; it was), BTGO's
  winner was kept at a 0.08R premium, UNTY defended and lost anyway.
  ASTE (+1.5R open) is the cost side: ripped off its level without
  the signature — a missed winner. Eric's carry-forward ruling: the
  retro cohort MOVES WITH THE BOOK — `grade_at_exits()` rides the
  shadow poller so the ~38 then-open trades grade at their real exits
  — and the running comparison prints BOTH cohorts labeled (retro =
  reconstructed 15m tape, n beside it; live = recorded bars,
  Monday-forward), never silently merged. Five `no_touch` rows
  (Polygon's 15m low never printed our recorded trigger; SYF −1.11R
  among them) render as holes for both variants. QUEUED, decided
  2026-08-22 but not yet shipped: a **defense premium cap** — v1
  "defended" FLXS at a 4.79% premium because a big first bar
  qualifies no matter how far it has run (every other defended
  premium: 0.04–1.5%); Eric: "eventually we will want a premium cap
  ... it runs too far away from the target entry." The cap's value
  gets picked from the recorded premium_pct distribution once enough
  rows exist, then applies to the detector definition as a graded
  change — not mid-test by feel.

- **A fill must carry its own evidence** (2026-08-27, the 770-fade
  audit — Eric: "so the trade was actually accurate and the tape was
  correct?"). Two of the morning's SPY gamma fills could not be
  certified from stored bars: the wall-fade entered without the wall
  ever printing (the hidden cause: `_touch()` carries a 0.1%
  tolerance — ±77¢ on SPY — so a 769.57 close "touched" 770; code-
  correct, but the rationale string said "after touch" and the record
  couldn't show which kind), and a flip-hold's entry price differed
  28¢ from the stored touch-bar close because two fetches seconds
  apart returned different values for the same just-completed bar
  (vendor settling) while `paper_spec_bars` keeps only the FIRST-seen
  version. The fixes: every new fill stamps `paper_trades.entry_bar`
  (decided-on bar, touch bar, and a `near_touch_tolerance` flag) so
  "was this fill valid" is answerable from the trade row forever; a
  one-shot 1-minute-tape forensic job (`analysis/fill_audit.py`,
  marker `fill_audit_v1`, verdicts in `fill_audit`) adjudicated the
  two questioned fills — research verification of the record, read-
  only over the books by tested signature. The tolerance itself is
  NOT retuned by feel: whether near-touch fades are a feature or a
  leak is a replay-harness question, and any change grades before it
  ships. tests/test_fill_audit.py pins the verdict logic (an entry-
  minute print is INCONCLUSIVE — 1m aggregates cannot order sub-
  minute events), the evidence schema, and the module's read-only
  signature.

- **The cipher exemplar museum: the eye becomes data, one labeled
  chart at a time** (2026-08-27, Eric on his premarket cipher entries:
  "we just haven't been able to master those on a mechanical level
  yet" → "build the MCP tool"). The standing BW-3D rule finally gets
  its mechanism: `watchtower_log_cipher(ticker, timeframe, take|pass,
  note)` — callable from ANY Claude session the moment a chart is
  judged — snapshots the system's own stored oscillator state (full
  component row from oscillator_scan, bar-stamped) into
  `cipher_exemplars`. PASSES ARE FIRST-CLASS: the mechanical
  definition lives in the boundary between takes and near-miss
  refusals, so both record with the same machinery. A missing/stale
  state is a NAMED hole (per-timeframe staleness: 1h ages in a day,
  weekly gets 8) and the label is kept anyway — the eye's verdict is
  data even when the snapshot missed. Gate: ~30 takes / ~20 passes,
  then the classification pass derives the entry definition from the
  set. The named archetypes (NFLX-3D, CHWY, UNH takes; AGO spent-turn,
  LNG no-level, NI/MARA knife, COLM/GOOS recovered-flow passes) are
  QUEUED for retro-seeding from recorded history — several were
  intraday reads needing historical intraday recompute, a research
  backfill. `watchtower_cipher_exemplars` prints the census.
  tests/test_cipher_exemplars.py pins normalization, per-timeframe
  staleness, pass-parity, and writes-own-table-only by signature.

- **The options-expression shadow: the wrapper is measured before it is
  traded** (2026-08-27, Eric: "we also need to be swing trading
  options... let's definitely build this"). The names are NOT chosen
  for options — the swing book's graded classes choose the names;
  `analysis/options_expression.py` only asks which signals options can
  EXPRESS and measures the answer: every swing fill gets the ticket
  the desk would buy (ITM call, delta≈0.70 when greeks exist else the
  ~0.85×spot strike, tenor by class — weekly 55-100 DTE, daily 28-50),
  priced from the live chain at entry and re-priced at the live
  trade's exit into `options_expression`. Refusals record WHY
  (illiquid <100 OI / no_chain / no_mark — a silent filter is a
  _social_block); marks are chain prints; spread cost is a DECLARED
  v1 hole (snapshots carry no bid/ask here) stated wherever the
  comparison renders; a missed same-day window is a 'hole', never a
  reconstruction. Eric's 2026-08-10 gate stands: the live options
  paper book opens only after the swing book's ~30 clean resolutions
  — this shadow exists so it launches calibrated instead of guessing.
  Same evening, the fundamentals question ("should we choose names on
  fundamentals as well?") got the doctrine answer — measure, don't
  argue: `paper_specs.fundamentals_state`
  (analysis/fundamentals_tag.py — Piotroski, Altman Z, days-to-
  earnings from tables the nightly FMP jobs already fill) stamps
  every swing spec AFTER curation, arming-blind by signature. Stated
  prior, honestly: the sector study showed price already embodies
  most slow information at this horizon — if the tag earns promotion
  it is most likely as a VETO/warning (Z<1.8 distress, earnings
  inside the hold), not a selector. tests/test_options_expression.py
  pins the picker, the named refusals, the tenor map, the write-only-
  its-own-table signature, and the writer's blindness.
  QUEUED expression test (2026-09-02, Eric, off the @SPXVIX NBIS read
  — "slipped under the 200 put wall, not selling puts here, that's how
  you get run over"): **short puts at the put wall**, the premium
  seller's expression of the same gamma map. Spec sketch, to be
  pre-registered when the gate opens: sell a short-dated put at/just
  below the put wall ONLY when the wall sits BELOW spot in a pinning
  (positive-gamma) regime — a put wall above spot or a slippery board
  is a hard VETO (the inverted-wall doctrine, now with a named
  counterparty lesson); grade vs the shares-equivalent per the
  expression scoreboard, refusals recorded (no_chain / illiquid /
  regime_veto). Same gate as every expression test: ~30 clean
  resolutions in the underlying first. Sits beside the gamma book's
  own fade expression (short call spread above the wall) as the
  second wall-located premium idea.

- **Gamma expansion is replayed before it is armed, and targets are
  shadowed before they walk** (2026-08-28). Two questions from the same
  session, both answered by measurement: (1) Eric: "meaningful netGEX
  on several other names — will the system trade those?" → the gamma
  book's universe is VENUE=[SPY,QQQ,IWM] BY DESIGN; the mega-caps are
  watched (drift/prox alerts) but never armed. `gamma_mega_replay`
  (one-shot, marker _v1) grades the EXACT live rules —
  build_gamma_specs + gamma_replay.simulate_day, nothing reimplemented
  — on the seven mega-cap boards since 2026-07-15 (~33 board days;
  research-fetched 15m bars). VERDICT (read the same evening):
  REFUSED — 5 trades in 33 days, all wall_fades, 1-4 for −14.28R
  (avg −2.86R; AMZN −8.83R on one fade). The failure mode is
  structural, not sample-luck: the 0.15% close-basis stop is
  index-calibrated — single-name momentum closes FAR beyond it, so
  losses run to multiples of the risk unit — and flip-holds never
  armed/triggered at all. NO gamma_single book. The mega-caps stay
  eyes (drift/prox alerts), not hands; any future single-name gamma
  book needs its own stop geometry graded first, the same bar QQQ
  faces for day-bias.
  (2) Eric: "the QQQ walls drifted — that is important data" → live
  trades FREEZE stop and target at entry while the board re-prices;
  `analysis/target_shadow.py` replays every resolved gamma trade from
  RECORDED bars + RECORDED 15-min boards under walk_both and
  walk_toward variants (stops never walk — that one is not a
  hypothesis). Daily 16:47 pass + retro backfill; the better variant
  over ~20-30 shadow-resolved trades ships as the rule; until then
  frozen stands. tests/test_target_shadow.py pins the walk semantics.
  RETRO VERDICT (first read, n=12 graded + 3 holes): FROZEN WINS and
  it is not close — live frozen targets +2.24R vs walk_toward −3.54R
  and walk_both −3.19R on the same trades. The physical reading: OPEN
  INTEREST sits at fixed strikes — the intraday "wall walk" re-labels
  max-gamma, but the morning wall's OI pool remains the actual magnet,
  so the remembered level out-trades the re-priced one. Small n; the
  16:47 pass keeps accumulating; but the prior flipped — frozen is
  not just the default, it is currently the measured best.
  Flip-hold context for reading results (live n=7): 3 of 7 reached the
  wall same-day — ALL entered before 10:30; all 10:30+ entries ended
  eod_flat (avg +0.22R, profitable); entry time and wall distance are
  the real variables, queued for the geometry gate once replayed.

- **The 16D below-zero green-dot study — Eric's GOAT claim,
  pre-registered** (2026-08-28, off the VFF chart: "every green dot
  indicates the bottom... within 3-6 months", refined live to dots
  BELOW THE ZERO LINE on stocks in MAJOR DRAWDOWN; VFF-16D is the
  named archetype and was onboarded — ticker + daily history — so the
  archetype cannot be a hole). Spec frozen before any number:
  event = 16D wavetrend cross-up with wt2 ≤ 0 at the cross; condition
  = drawdown vs 2-yr high bucketed <30/30-50/50-70/70+; outcomes =
  distance to fwd 6-mo low, lower-dot-followed within a year
  (first-dot vs later-dot bottoms), 3/6/12-mo forward returns;
  baselines at readout = random days on the SAME drawdown cohort +
  era split + the survivorship caveat stamped (currently-listed
  universe — the corpses' dots are unseen). Bars are FIXED-ANCHOR
  16-trading-day blocks on SPY's own calendar index — end-anchored
  resamples repaint (the BW-3D lesson) and
  tests/test_greendot_study.py pins the no-repaint property, the
  zero-line leg, and writes-own-tables-only. Chunked fleet seeder
  (greendot_progress resume); dots in `greendot_dots`. Same session's
  smaller catch: the options shadow's weekly DTE window (55-100)
  called VRTX "no_chain" because a monthlies-only ladder's nearest
  suitable expiry sat at 49 DTE — windows widened (40-115 / 24-60)
  plus a widened retry that records `widened_window` when it saves
  the ticket.

- **The green-dot verdict and its screen** (2026-08-29, the study's
  readout): Eric's GOAT claim graded TRUE as an expectancy engine, not
  a bottom-ticker — on 50%+/70%+ drawdown stocks, below-zero 16D dots
  pay +5.8/+10.0% median 6-mo (56-57% positive) vs −10.6/−4.6%
  (38-40%) for random days on the SAME cohort; the dot's median path
  still dips another 17-25% first (only ~17-21% of deep dots print
  within 5% of the forward low); the FIRST dot is the LAST dot ~70% of
  the time; CROSS DEPTH is the throttle (≤−30 pays +7.7/57%; shallow
  crosses NEGATIVE — the LNG level-is-the-signal lesson at 16D);
  era-stable in direction, magnitude decayed post-2016 (+6.8 vs +24.5
  med); averages (+94%/+190%) are lottery-skew + survivorship, read
  medians. VFF's own three dots are the whole distribution: a rally-
  not-bottom, a knife, and the launch dot — which carried the deepest
  cross (−37). Shipped: `watchtower_greendot` (fresh deep-cross dots
  on ≥30% drawdown, priors + survivorship stamped in the render, a
  quiet screen is a reading), nightly 23:20 upkeep (dots append only
  when a 16D block completes — block-boundary claim per id — and
  forward outcomes fill as history arrives), and 16d as an exemplar-
  museum timeframe (state computed on demand from the SAME fixed-
  anchor bars, so exemplar and study speak one dialect). Doctrine for
  reading the screen: the dot forecasts the bottom's ERA — expect
  adverse excursion, size in PRE-PLANNED tranches with a fixed total,
  never open-ended averaging ("DCA until it turns" unbounded is
  martingale into the ~43% that keep dying); the tranche-schedule
  question (lump vs laddered adds) is QUEUED as a follow-up grade off
  the stored dots' forward paths.

- **The green-dot entry-schedule verdict: confirmation is priced at
  the 16D scale, and the ladder is a risk choice, not free money**
  (2026-08-29, the readout of Eric's HA-doji question — all 20,404
  dots graded across five pre-registered variants, zero holes,
  entries always at REAL closes). On the deep cohort (dd ≥ 50%,
  cross ≤ −30, n=3,718): the HA doji-then-green rule fires on only
  43% of dots, misses ~29% of the RUNNERS outright (no signal inside
  6 months on dots that ended higher), pays a +9.3% median premium
  over the dot close, and still eats −17.6% median MAE vs −19.5%
  entering at the dot — two points of drawdown bought for nine of
  premium and a third of the winners. The strict break variant is
  strictly worse (+22.5% premium, MAE −19.2%); first-raw-green saves
  nothing (MAE −20.0%). The physical read, CORRECTED same day on
  Eric's pushback ("the sixteen day dot doesn't mean it's spent...
  those run forty, fifty plus percent"): he's right — deep dots are
  NOT spent (44% run 30%+ within a year, 35% run 50%+, 18.5% run
  100%+, median runner +86.5%; a 9% premium is trivial against
  that). The refusal stands on SELECTIVITY, not exhaustion: the
  doji-green fires on 37.7%-run-50%+ dots vs 33.2% on never-fired —
  barely sorts — and the 100%+ monsters are EQUALLY common among
  dots where it never printed (18.3 vs 18.7%): the biggest movers
  rip without pausing to print the pattern (the ASTE lesson at 16D).
  One bar of proof at this scale buys a third of the runners missed
  for ~4pts of sorting.
  The 8/21 EMA-reclaim grade (same day, Eric's rule frozen verbatim:
  price above BOTH EMAs, no cross): DAILY fires on 99.9% of deep
  dots at +1.3% median premium — it is the dot with extra steps,
  changing nothing; WEEKLY is marginal (~1pt of MAE saved for ~5pts
  of premium; the predicted "sweet middle" does not exist); 16D is
  the one rule tested with REAL selectivity — reclaimed dots run
  50%+ from the dot 54.3% vs 18.0% never-reclaimed, 3x and
  era-stable (52/16 post-2016, 60/22 pre) — but it charges a +33.3%
  median premium to know (partly definitional: clearing 16D-scale
  EMAs off a washout IS a ~33% rally), so its from-entry outcomes
  are the board's worst (med 12-mo +10.2% vs the dot's +19.8%).
  Verdict: FILTER, not entry — buy the dot (ladder or lump per the
  risk choice above), read the 16D reclaim as the hold/conviction
  signal that the dot is a trend-changer. QUEUED as its own graded
  question: does ADDING a tranche on the 16D reclaim beat holding
  original size?
  The doji-green sequence remains a legitimate READ that the turn is
  here (the exemplar museum grades that eye); it is refused as an
  entry-price mechanism. The bounded ladder (1/3 at dot / −15% /
  −25%) grades exactly as Monday's doctrine guessed: per DEPLOYED
  dollar it wins everything — MAE −12.8% vs −19.5%, median 6-mo
  +13.0% vs +7.0%, 64% positive, era-stable, only ~69% of budget
  ever at risk — but per COMMITTED budget the lump's capped mean
  beats it (23.2% vs 15.1% at 6 mo), because the ladder underweights
  the runners (1,353 dots never dipped 15%: one tranche in, +36.7%
  median) and concentrates into the sinkers (1,646 filled all three:
  −8.9% median). Both are true and both render wherever this
  surfaces: ladder = shallower drawdown, cash in reserve, better
  hit rate; lump = more raw expectancy per dollar committed. Stated
  caveats: survivors-only universe, capped means, ladder outcomes
  measured on final avg-px basis from the dot date, no costs. And
  the session's query lesson, kept: Postgres LEAST/GREATEST IGNORE
  NULLs — LEAST(fwd6m, 300) turns a data hole into +300% — so every
  capped aggregate guards NULL explicitly (the med-11.8-vs-7.7
  reconcile catch; numbers on one line must reconcile).
  ROBUSTNESS (same day, Eric's 3W-chart question — a 3W bar is 15
  trading days): the whole spec re-blocked at 15-day fixed-anchor
  blocks (analysis/greendot_robust15.py, tables greendot_dots15,
  find_dots/bucket imported never reimplemented) and the edge
  SURVIVES — matched-ticker deep cohort med 6-mo +5.9/+12.8% by era
  vs 16D's +7.4/+13.6%, win rates within 1.5pts, dip-to-low −20% on
  both grids, and the depth throttle replicates (deep +7.4 vs
  shallow +3.3). The signal is the ~3-week compression, not the
  number 16; 3W and 16D charts are interchangeable reads. Individual
  MARGINAL dots flicker between grids (ACHR's false signal) — deep
  dots are grid-stable, one more reason cross ≤ −30 is the
  load-bearing leg.

- **The clock-speed verdicts: the dot is a ~3-week signal, and the
  8/21 clear is a sorter at every scale, never a fill** (2026-08-29
  evening, Eric's compounding question — "smaller moves on a daily
  bounce or weekly bounce, or hold longer with the 16d?"). The same
  dot definition re-run at daily and weekly bars (greendot_dots_ms,
  fixed daily-horizon outcomes): DAILY dots are noise (med 126d
  +0.85%, 51.6% — and still eat a −16% quarter excursion: a faster
  chart samples the same slow bottoming, it does not escape it);
  WEEKLY dots are a decayed dead zone (post-2016 med 126d +2.0%,
  51.6% vs 16D's +7.1%/56.2% on matched tickers — and the gap
  WIDENED after 2016); the 16D wins per dollar-day at every horizon
  before even counting the ~6x cost differential of fast cycling.
  Eric's 8/21-reclaim rule, graded at three scales
  (greendot_ms_align): a SUPERB knife detector everywhere — daily
  cleared-vs-never spreads +6.3 vs −9.6 (126d), weekly +21.0 vs
  −17.9 (252d), 16D 54% vs 18% run-50%+ — and a losing ENTRY
  everywhere, because the premium scales with the timeframe (5.6% /
  16.2% / 33.3%) and always consumes the sorting edge (weekly-clear
  entries are NEGATIVE at 63d: the clear IS the bounce). One law:
  enter at the dot, read the clears as conviction/triage on holdings.
  The gated ladder both ways: v1 (any reclaim before the touch)
  VACUOUS — binding 0.1%, a gate that cannot fire; v2 (reclaim
  within 15 days, the graded knife test) binds 9.8% and HURTS (med
  6-mo 13.7 vs 14.5, no MAE saved) — the daily knife signal does not
  transfer to 16D adds, whose cheapest fills live exactly in the
  not-yet-reclaimed dip. The PLAIN ladder stands, having beaten two
  informed attempts to improve it. The block-size sweep (nd3-nd32,
  exploratory, both-eras + agreeing-neighbors bar) reads out
  separately; partials so far: 3d dead like the weekly, 8d
  half-alive (trails 16D both eras at 12mo).
  SWEEP VERDICT (same night, curve complete but nd32 partial-n): NO
  CHALLENGER. Post-2016 the curve climbs weekly→15 then PLATEAUS
  16-26 (~7.0-7.6% med 126d, 56%); pre-2016 it PEAKS at 15/16 (11.5)
  and rolls off (21: 9.6, 26: 6.1). Under the pre-registered bar
  (beat 16 in BOTH eras, neighbors agreeing) 21/26 fail — half a
  point of modern-era noise against a clear old-era loss — and
  slower blocks find 40% as many dots for zero both-era gain. 16 is
  the only size at/near the top of both curves: Eric's eye found,
  years ago, the number an exhaustive sweep could not improve. The
  timeframe question is CLOSED absent a regime change. And the AI-capex basket
  (watchtower_ai_capex) ships Eric's taxonomy — NVDA bellwether +
  the capex layer, software platforms out — as one breadth line;
  membership pinned by test, warnings on longs, never shorts.

- **The Tape Bot trades nothing; the eyes stay eyes** (2026-08-29,
  the retest-machine study — Eric, the night the indicator went live
  on his charts: "should we add a version of it to our autonomous
  trading?"). The v2.2 Pine state machine ported VERBATIM
  (tests/test_tapebot_retest.py pins two Pine-order truths the port
  surfaced: a crossing close re-arms the machine the other way, and
  the wick refusal manifests as a flip, never a signal) and graded at
  PDH/PDL on the stored SPY/QQQ 15m record — 21,419 signals, entries
  at the alert bar's close, outcomes to the true daily close
  (tapebot_retest_events, migration 032, marker tapebot_retest_v1).
  VERDICT: REFUSED as an autonomous entry. Every event x era x
  ticker cell is a coin flip — 42-55% win, avg/median inside +/-10
  bps, MFE ~= MAE — and the pre-registered cuts (time-of-day, level,
  open-above conditioning) all fail the both-eras + replication bar:
  the best survivor (SPY PDH retest, open-above, post-10:30 —
  +9.5/+3.5 avg bps by era) dies on QQQ (-1.5/+0.6); the short
  events grade 42-47%, negative AGAIN. The reading: the machine is
  an honest NARRATOR of level fights, not an edge — it takes every
  confirmed break-retest regardless of day shape, re-arms all day,
  and holds no first-touch discipline; the only PDH-retest trade
  that grades remains the day-bias definition (open-above + the
  10:30 first-touch cancel + one trade/day), which is ALREADY the
  live day_bias book. ONH/ONL phase 2 (premarket backfill) NOT
  earned by phase 1. The Tape Bot's job is what it already does:
  put the desk's rules on Eric's charts and page him on level
  breaks — eyes and alerts, not hands.

- **The frequency night: selection is the edge, entries are commodities,
  and the stop wants the wick rule** (2026-08-31, the evening after the
  chop-day beating — Eric: "find the most liquid names for options and
  test them... which entries are the best... and where the best stop is.
  I will be mechanical with this."). Three pre-registered studies read
  out the same night, all on 2 years of 1m bars (11 liquid names:
  scanner 7 + rest of mag-7; ~2.1M bars; year-half split + per-name
  replication as the bar; entries at closes, no costs, stated).
  (1) **RS-leader** (rs_leader_events, marker rsleader_study_v1): the
  mag-7 name leading QQQ by >=0.4% at 9:45, bought on its first 1m
  8/21 pullback that CLOSES holding (9:45-11:00), held to the close,
  pays +0.45R avg (capped +-10R — the outlier lesson applied), ~52%
  positive days, POSITIVE IN BOTH HALVES AND ALL 7 NAMES. The
  pullback is the entry (owning the leader from 9:45 med +0.02/+0.09
  only), leadership is the selector (midpack same entry sign-flips by
  half), and the 2R-target expression FAILS replication (4/7) — the
  edge wants the day, not a scalp. Laggard short: refused, era flip.
  (2) **Tape-entry** (tapeentry_events/days, marker tapeentry_study_v1):
  UNCONDITIONED, every Bot/Scanner entry family on the 11 names is a
  coin flip — best cell (the v1.7 1m-gated long) +2-3 bps avg, and
  the retest families do NOT beat their chase controls (Eric's
  retest-is-best assumption graded and refused as stated; the tapebot
  index verdict replicates on single names). But JOINED to leader
  days the same 1m-gated entry turns +7 to +21 bps across every stop
  variant — selection transforms a commodity entry. Medians negative
  with positive averages everywhere: the profit is the right tail,
  one more reason not to cap winners.
  (3) **The stop verdict** (the stops JSONB grid, graded on the same
  entries): the tight touch-based structural stop under the 1m
  pullback bar is a whipsaw machine (86% stopped unconditioned, 38%
  stopped-then-green; on leader days +7.2 bps). The SAME LEVEL under
  the wick rule — exit only on a 5m CLOSE through — nearly doubles
  leader-day expectancy (+12.6 bps, whipsaw 29%); a 1% disaster cap
  whipsaws only 8%; the 21-EMA-close trail has the best average
  (+21) and the worst median (-6) — the aggressive variant. Single
  stocks want confirmed closes at the stop, exactly as the defense
  study read (and opposite the index, per day-bias — both stay true).
  The 🎯 alert carries the close-rule stop verbatim.
  Shipped the same night: alerts/rsleader_ping.py (🧭 9:31 flip
  proximity, 🏁 9:45 rank/stand-aside, 🎯 per-minute GO watch firing
  at the 1m candle close — definitions IMPORTED from the graded
  study, partial bars dropped at the data layer, at-most-once,
  restart-safe); the flip-proximity read (flipprox_days: opens within
  0.3% of the flip cross it ~1.3-1.5x/day, beyond 0.6% ~0.1x — the
  "is it hugging the flip" question is partially knowable at 9:30;
  small n, measurement only); trade_journal + watchtower_journal_log/
  watchtower_journal (Eric's manual book, any-session writable, Grok
  relay allowed to THIS table only, R never fabricated, losers lead);
  and analysis/index_bars_daily.py — because the night also found
  index_intraday_bars FROZEN at Aug 21 (only the one-shot backfill
  ever wrote it; flipprox missed the exact chop days it was built
  for, rsleader stranded 6 days short of its marker): a research
  table nobody appends to is a _social_block with a date on it —
  every stored record needs an owning job, and the 16:20 cron +
  boot catch-up is this one's. The LEAST/GREATEST-ignores-NULLs
  trap fired again (ema21x r=NULL averaged as 10.0R) and was caught
  by this file's own note — the reconcile rule works.
  SAME NIGHT, the hybrid-exit follow-up (Eric: "why can't we do the
  hybrid test now?" — no reason; hybridexit_events, marker
  hybridexit_v1, exits re-simulated on the SAME graded entries): the
  TRAIL-AFTER-1R variants are the only exits positive in BOTH
  year-halves on both bps and R at leader days (21-EMA-close trail
  +19.6/+13.4 bps, +0.40/+0.27 R, ~40% win; 5m-low ratchet the
  near-equal twin at 42-43% win), beating the fixed hold-to-close
  (which flips negative in half 2 on R in this frame) and confirming
  the stop grid's independent EMA-trail read. Breakeven-after-1R is
  the graded TRAP of the family (17% win, half-2 negative — the
  entry-level stop sits exactly where the normal afternoon pullback
  prints), and the 2R bracket stays refused. The 🎯 alert carries the
  full lifecycle verbatim: close-rule struct stop + 1% disaster
  touch-cap, then at +1R the 21-EMA 5m-close trail, bell exit for
  survivors. One live rule, one alternative (the ratchet), no
  switching by feel.

- **Eric's manual R is $250** (set 2026-09-01, the night the RS-leader
  rule went live): fixed dollar risk on EVERY manual trade — system and
  freelance alike — sized as risk / per-unit stop cost, rounded DOWN,
  skip when one unit exceeds the budget. It changes only at a flat,
  scheduled, market-closed review by a one-sentence reason, never
  intraday and never after a loss. The journal grades outcomes in R
  against this baseline; a size that drifted from it is a `mistakes`
  entry, not a footnote.

- **The pings and the ledger are one definition, and the audit checks
  that they agree** (2026-09-02, the 11:09 phantom exit ping): the 9/1
  partial-block fix cured the BOOK, but the Discord trade-watch
  carried its own COPY of the lifecycle and the copy kept the bug —
  it announced META's exit at 11:09 (mid-block) while the book's
  rule-correct exit printed at 11:39 (593.73, +0.98R; the 11:35-11:39
  block closed 97¢ under the day-anchored 21-EMA at 594.70). A second
  definition is a second place for the same bug to live. Now: the
  watcher IMPORTS rs_leader_book.lifecycle_state;
  tests/test_one_definition.py forbids any alerts/ module from carrying
  resample/trail math of its own; and ledger_audit.reconcile_pings
  checks every 🚪 against the recorded exit bar nightly (a ping that
  precedes the bar, a ping with no exit, an exit with no ping → 🚨).
  The chart-line lesson rides with it: a continuous 21-EMA (yesterday's
  and premarket bars included) sat several dollars BELOW the book's
  day-anchored line all morning — Bot v2.7 draws the book's line (WT
  TRAIL) so "did it close below?" has one answer on every screen.
  Same evening's gamma slate (Eric, off the SPXVIX NBIS card: "help me
  see where the strongest walls actually are"): compute_gex reports
  each wall's weight, SHARE of its side, and next-strongest strike
  (wall_strength JSONB beside every board row; the 🌅 board and 📍
  prox alert print it — a wall holding half the side is a fortress,
  15% is a label); a bounded strike x expiry grid persists for the
  drift set at every sweep/re-price (gex_strike_expiry: ±6% of spot,
  ≤60 DTE, cells ≥ $5M — absent = below floor, never zero); the 🌅
  board carries a top-strike ladder; /dashboard/gamma renders the grid
  with walls/flip/regime overlaid (a display of the board, never a
  signal source; on-demand tickers are point-in-time and say so); and
  the **wall-touch prior** (wall_touch_events, 16:50 nightly + boot
  backfill, pre-registered: the day's first intraday board, levels
  within 3%, touched = a completed bar containing the level, holes
  NULL) answers Eric's 📍 question — "did the alert fire because we
  were likely to get there?" (no: it fires on arrival) — with a per-
  level touch rate + n on the morning board, small-n stated, record
  since 2026-08-19. Also shipped: 📒 books scoreboard to #desk at
  16:59 (running record per book, worst first, plus the morning-board
  vs live-board gamma head-to-head and the day's disagreements —
  Eric: "let's run both and see which wins", gate ~20-30 each); the
  trailvar2 study (Kalman 5m level, MAD trail — research #11/P2-21)
  and the chase-premium study (fills at GO close + 0/.10/.25/.50/1.0 x
  risk, granted only where a bar traded there, R on the chaser's
  wider risk — the 🎯 gets a stated tolerance instead of "the candle
  looked far"); and the day's freelance journal rows. Slipped, stated:
  VWAP band touch statistics (#19) — Thursday with the IB study.

- **The book's exits were graded on someone else's entries** (2026-09-02
  late, found by the chase study's f=0 baseline — the first time the
  live rs_leader lifecycle was ever run on the rs_leader GO population).
  The RS-leader study graded HOLD-TO-CLOSE on GO-pullback entries
  (+0.45R); the stop grid and hybrid-exit study graded the exits on the
  tape-entry study's 1m-gated entries (+0.40/+0.27R for the 21-EMA
  trail); the book shipped the first population's entry with the
  second's exits and nobody asserted the join. Graded on the 446 GO
  entries (2-year 1m record, closes as fills, no costs, ±10R cap): the
  live lifecycle — struct stop on 5m closes + 1% disaster touch + trail
  after +1R — is **−0.21/−0.27R by year-half, 35% win**, while
  hold-to-close on the SAME entries is **+0.38/+0.52R**. Every exit
  rule loses to holding on this population: stopped trades average
  −1.86R at the stop vs −0.83R held to the bell (a 5m close through a
  0.27%-wide stop is where the normal 9:50 shakeout prints, not where
  the day is decided), disasters −3.4R vs −1.06R held (a 1% cap on a
  0.27% risk unit is a −3.7R exit, not a −1R one), and trail exits
  +0.86R vs +1.18R held. The same exit on the tape-entry population's
  pre-10:00 entries grades +0.60R — the exits are not broken, the
  transfer was. Same-evening fix to the chase study's population
  (`no_pullback_945` control rows were half its sample — deleted; the
  query now says `entry_kind='go_pullback'`). The exit re-grade
  (`analysis/rsl_exit_study.py`, table `rsl_exit_events`, marker
  `rsl_exit_v1`) runs six DECLARED variants through the book's own
  `lifecycle_state` with keyword switches (`struct_stop`, `trail`,
  `arm_px` — research-only, defaults are the live book, disaster never
  switchable; tests/test_rsl_exit_study.py pins each switch): book,
  hold, disaster-only, disaster+trail (no struct stop), struct+bell (no
  trail), and wide5 (stop under the five-minute window ending at the
  GO bar). Every variant
  reports R on the GO risk unit so they compare on one scale. NO LIVE
  CHANGE until it reads out and Eric rules — the book is paper, the 🎯
  is what he trades, and a rule that changes tonight by feel is the
  same mistake with the sign flipped. The doctrine, restated: a
  lifecycle is entry × exit × population; grade the product, not the
  factors.

- **Eric's entry rule: let the trend identify itself** (2026-09-02, his
  own diagnosis after two live days — losers GOOGL put pre-rank, MSFT
  mid-pack chase, QQQ put on an ANTICIPATED 15m lower high; winners
  META (confirmed 1m hold) and SPY calls after 760 AND 765 were
  reclaimed on closes): "so far my losers have all been when I don't
  let the trend identify itself and try to get in early." The idea may
  be early; the position may not. The desk's record says the same
  from three directions — structure shorts retired (728 episodes, net
  negative every regime), the 9:45 flush is chop while the post-10:30
  retest wins, and unconfirmed retests lose to defended ones. The
  journal grades this line at ~30 trades; n=6 today, 3-for-3 each way.

- **A new book must be BORN into the schema, and a placeholder is a
  live number to every reader** (2026-09-01, the rs_leader book's
  first live day). Three silent refusals in one morning, all the same
  disease: the paper_specs book CHECK, then paper_trades fill_kind
  and exit_reason allowlists — written for the incumbent books —
  bounced the new book's writes with no error surfacing anywhere
  (specs armed in code, refused at the door; the assert-admission
  lesson at the schema layer). Then the inverse failure at 11:40: the
  spec's PLACEHOLDER target (entry*1.02, written only to satisfy NOT
  NULL) was executed by the generic trade poller — which manages real
  targets for swing/gamma and was excluded for day_bias but not for
  rs_leader — closing the live META trade at +12.62R under
  exit_reason 'target', a reason the book's rulebook doesn't contain.
  Eric caught it from his phone ("that doesn't seem right"), the
  dashed line at 578.08 = entry*1.02 confirmed it, and the trade was
  REOPENED same session per the AGMB retroactive-convention
  precedent (decided on recorded bars; note in the row). The rules
  that came out of it: new-book birth touches FOUR places (schema
  allowlists via migration, poller exclusion, sentinel 999999 target
  — never a plausible price — and tests/test_liveday_fixes.py pins
  poller exclusion + sentinel by signature); and analysis/
  ledger_audit.py re-earns "no defects" EVERY night — per-book
  exit-reason legality, every entry AND exit price verified inside
  its day's recorded bar range, holes counted as holes — quiet when
  clean, loud in #desk when not. The first full audit (all books,
  all history, same day) came back CLEAN: 27 closed + ~80 open
  trades, zero anomalies — the incumbents co-evolved with their
  schema; only the newborn was ever refused.
  Same day, live-day upgrades from Eric's own reads: the 🎯 GO alert
  now SIZES the trade (R_DOLLARS=250 baked in — contracts at 0.70Δ
  ITM and ~0.55Δ ATM, round-down, SKIP line when one contract
  exceeds budget); Scanner v1.8 (Pine v6, calc_bars_count pinning
  all 28 hidden streams — kills both the big-chart memory crash and
  the labels-differ-by-host-chart inconsistency he caught); Bot
  v2.4 momentum honesty (completed-bar legs — the forming bar's
  partial volume zeroed the volume leg at the top of every bar —
  plus an IGNITION leg for fresh bursts and hysteresis on the chop
  gate so TRENDING/RANGING stops flickering at the 4-cross
  boundary). Day one's human-vs-machine reading, n=1, stated: his
  manual META exit +9.4R at PDH resistance vs the trail still
  holding into the afternoon; his two freelance trades (pre-rank
  GOOGL put, mid-pack MSFT OTM call) −$216 vs the system trade
  +$752 — selection was the edge, live, on real money.

- **A partial resample block is a forming bar wearing a completed
  bar's clothes** (2026-09-01 afternoon, Eric's dotted line at
  581.46): the rs_leader trail exited META at 14:27 — a mid-block 1m
  close — because lifecycle_state mapped res5's trailing PARTIAL
  block as if it were a 5m close; the rule-correct exit was the
  completed 14:25-29 block (581.455 at 14:29, recomputed from
  rsl_book_bars with the study's own code). Half a cent today; a
  wick-rule violation always, and a live/backtest divergence (the
  backtest read full days, so every block was complete — the live
  per-minute port introduced it). Row corrected per the AGMB
  retroactive precedent (+16.38R final); e21 now maps COMPLETED
  blocks only (a later block proves completion; the trailing block
  needs its final minute); tests/test_liveday_fixes.py pins the
  mid-block refusal. Both of day one's phantoms were caught by Eric
  reading a price line on his chart — the audit's cheapest layer is
  a human asking "what printed at my line?".
  Same evening, the day's study readouts: **rank-ladder** — a
  QUALIFIED #2 grades exactly like #1 in aggregate (+0.40R, both
  halves, n=281 vs 442) but fails per-name replication (4/7; AAPL/
  AMZN/META negative) → candidate under observation, NOT a second
  entry; the ladder cliffs at #3 (−0.38R, half-2 −1.04). MSFT-at-#2
  is historically one of the BEST cells (+1.35R, 69%) — 9/1's
  failure was its 31%, and the graded #2 trade is the 1m GO in
  shares, not a 5m-retest OTM call. **Trail variants** — chandelier
  (−0.06R) and ER-gated trail (+0.08R) both fail the both-halves bar
  on leader days; the incumbent 21-EMA trail-after-1R (+0.40/+0.27)
  beat its second and third informed challengers. **HOD/LOD map** —
  day extremes live at the EDGES (U-shape): on open-above days the
  LOD prints pre-10:30 at ~2.4x random and the HOD prints in the
  LAST hour at ~2x random (close_pos 0.60) — the structural reason
  trail-to-close keeps beating early exits. And **trend5_on was
  vacuous by construction** — the 5m gate needs 21 completed 5m bars
  (~11:15) while entries end at 11:00: all 3,465 rows read False
  (the wma_touch disease in a study column). Redefined as the 1m
  gate at the GO bar with NULL inside its own warmup (unknown is
  never False), rows wiped and re-graded; and the study's completion
  marker no longer demands zero todo days — with the bars marker
  present, a day the final record cannot grade (five half-days) is
  a HOLE, not pending work, else the marker literally could never
  write. Smaller keeps: momentum cells on both Pine panels carry
  direction arrows (GOOD ▲/▼ — the legs are force, mostly
  direction-blind; arrow rides the DISPLAYED string only, never the
  compared one, or the READY alert dies silently); a 9:45
  prep-sizing range was built and REVERTED same hour at Eric's call
  ("if it's not possible to know ahead of time I don't want to make
  it more confusing") — the 🎯 stays the only sizer; the 🌅 morning
  gamma board posts to Discord at 8:05 (7:30-sweep marks, inverted
  walls warned, holes named, boot catch-up idempotent by claim); and
  the first scheduled ledger audit ran clean — 107 trades, zero
  anomalies. Eric's standing ruling on the swing book's 1-13 start:
  "keep everything as is until we hit the gates" — no knob turns
  before the ~30-resolution clauses decide.

- **The measurement harness matches what the idea changes** (ratified
  by Eric 2026-08-22: "I will yield to your ideas on these... Let's do
  it"). Three harnesses, no more: a FULL SHADOW only for ideas that
  would produce a different trade (defense entry, confirmation, the
  binary skip) — counterfactual simulation earns its complexity only
  when the entry/skip itself is the question; a TAG for ideas that
  would merely filter or rank the same trades (cipher, sector) —
  stamped after curation, graded on the book's own resolutions, zero
  added machinery; FORWARD RETURNS for screens (cipher_reversal, the
  %R family) via alert-performance. Do not promote a tag to a shadow
  book for rigor's sake — five shadow books are not five times the
  science, they are five times the phantom-fill surface area. QUEUED
  behind two gates: a small dedicated paper book auditioning
  cipher_reversal as an ENTRY strategy (~5 slots, own rules, same
  fill honesty) — opens only once (a) its alert-performance forward
  returns keep confirming and (b) the swing book has its ~30 clean
  resolutions. One clean answer at a time; week one showed what
  happens when experiments share a book.

- **The day-bias study: bias is knowable, timing is the edge, the
  short mirror fails** (2026-08-23, Eric: "figure out the best possible
  way to figure out the daily bias... and then the best entries").
  Data: 5,443 SPY days (daily_prices) + 141k SPY 15m RTH bars
  2005→present (`index_intraday_bars`, backfilled via
  analysis/daybias_bars.py, marker daybias_bars_v1; QQQ full from
  2011, IWM intraday HOLEY on the vendor side — recorded, excluded;
  the first backfill "completed" with 7 bars/day because Polygon caps
  one aggs response at ~5,000 rows regardless of the limit param —
  windows now 60 days with a loud truncation guard). Era-split
  2005-2015 vs 2016-2026 on every headline number. The reads: (1)
  prior close in top 20% of range → 77% touch PDH next day, 49%
  break-and-hold (weak close mirrors: 71% touch PDL); (2) open beyond
  the prior range = acceptance — open>PDH closes above prior close
  79.5%, touches PDL only 12% (mirror below PDL); flat opens are a
  52/48 coin flip — the no-trade read; (3) open-above days offer a
  PDH retest 72% of the time, median 9:45 — and the 9:45 flush is
  CHOP (52-56% win, MAE>MFE) while the retest arriving AFTER 10:30
  (n=273) wins ~69%, +27bps to close, MFE~2x MAE, era-stable
  (73%/66%): the level that held all morning is the entry, the
  opening flush is not; (4) 15m-close confirmation does NOT pay at
  the index (82.5% confirm rate x 21bps premium outweighs the shallow
  −59bps knives — the OPPOSITE of the single-stock defense read, and
  both are true); (5) the SHORT mirror fails — blind PDL fade 49.8%,
  confirmed NEGATIVE (46.1%, −1.7bps): downside BIAS is real (76%
  close below prior close) but the retest-short ENTRY doesn't grade;
  index drift + reclaim squeezes. Caveats where the numbers surface:
  SPY underlying bps, close-anchored outcomes, 15m granularity, no
  costs; options expression is the separate layer per the
  ledger-grades-the-signal rule. QQQ replication (2026-08-23, 3,877
  days, same definitions untouched): the BIAS layer replicates almost
  digit-for-digit (open>PDH → 78.2% vs SPY 79.5%; strong close →
  79.6% touch PDH / 50.5% break-hold vs 77.2/48.8); the timing effect
  replicates in DIRECTION (early retest 51.9% chop with MAE>MFE, late
  62.6%) but at roughly a third of SPY's magnitude (+9bps vs +27,
  MFE/MAE ~1.1x vs ~2x — QQQ's fatter MAE eats the edge); the short
  mirror fails again (early PDL fade 43.6%, ~0bps). Verdict: the
  structure is market-wide, the TRADE is SPY-first; QQQ needs its own
  stop geometry before it qualifies. Next steps queued IN ORDER: a
  morning-brief day-bias line (states + probabilities, measurement
  only), a short-side phase-3 mechanism study, and only then a paper
  audition book (harness doctrine applies).
  The short-side mechanism study (2026-08-23, same evening): FOUR
  mechanisms graded on the stored bars and every one refused. PDL
  breakdown continuation from inside the range: negative, and WORST
  with the obvious weak-close bias behind it (39-43% win, −16 to −21
  bps — the crowded breakdown mean-reverts); opening-range-low
  breakdown on gap-down days: coin flip (49.9%, +1.2 bps); the late
  (≥10:30) PDL fade looked real in aggregate (59.2%, +37.9 bps,
  n=196) but FAILED the era split — 68%/+54 bps 2005-2015 vs
  50.0%/+20.7 2016-2026 — and failed QQQ replication outright
  (48.9%, +0.5 bps): a pre-2016 artifact, not tradable structure.
  VERDICT: the desk has NO graded intraday index short entry; the
  short side of a down-bias day is expressed by NOT taking longs
  (the flat-open stand-aside) and by gamma mechanics, not by fading
  or chasing levels. Do not re-litigate this by feel — a new short
  mechanism needs a new study that clears era-stability AND
  replication, the same bar the long side cleared.
  The audition book is LIVE (2026-08-23, `analysis/day_bias.py`, book
  `day_bias`, first trading day 2026-08-24): one spec/day, SPY only,
  trading EXACTLY the graded definition — arm only if the 9:30 bar
  OPENS above PDH ('skipped_bias' recorded otherwise; the stand-aside
  is a decision), resting limit at PDH live only from 10:30 (an
  earlier touch CANCELS the day — the graded chop bucket is not
  traded), exit at the TRUE daily close, fills/cancels/stops decided
  on recorded 15m bars only. One declared deviation from the graded
  trade: a 0.75% disaster stop on 15m CLOSES (wick rule) guards the
  tail the EOD-only backtest rode through. Scoreboard in underlying
  bps beside R; promotion gate ~30 resolved days, and the 0-2 DTE
  options expression stays a separate graded layer per the
  ledger-grades-the-signal rule. tests/test_day_bias.py pins the
  decision core (early-touch cancel, late fill at PDH, wick-excused
  disaster stop, stand-aside) and book isolation by signature.
  First cancelled_early recorded 2026-08-25: the 10:00–10:15 bar
  printed 765.12 through PDH 765.22 — the level failed its morning
  proof and the book stood down, per definition. QUEUED the same day
  (Eric, on asking whether a reclaim re-arms — it does not, the day
  is done): grade the **early-touch-reclaim variant** — on days
  cancelled for a pre-10:30 PDH touch, does a post-10:30 15m CLOSE
  back above PDH (a true reclaim, wick rule) grade positively to the
  close? The stored index_intraday_bars answer it; the adoption bar
  is the same one the long side cleared — era-stability AND QQQ
  replication. If it grades, it becomes a SECOND declared entry with
  its own arming rule; if not, the cancel stands vindicated. Until
  graded, cancelled_early days stay untraded — no re-arm by feel.
  The 📐 verdict ping (2026-08-25, built the evening of the first
  cancelled_early): `alerts/day_bias_ping.py` posts one #desk message
  per day at 9:51 — ARMED (with the full playbook: level, 10:30-only
  window, stop, prior with n) / STAND-ASIDE (zero is data) /
  unavailable (a hole is a hole) — and announces an early-touch
  CANCEL the tick the record shows it, because the first one happened
  silently and Eric had to ask. Read-only by signature, at-most-once
  per (kind, date) via discord_notify_log; tests/test_day_bias_ping.py
  pins the formats and the module's inability to write the books.

- **FMP news cuts are a budget, not a policy** (2026-08-23, after the
  90%→96% rolling-30-day usage warnings): NEWS_FMP_LIMIT defaults to
  250 and fetch_recent_news caches 150s per lookback (PR #217) —
  chosen because every 5-minute scan was re-downloading up to 1,000
  mostly-identical articles, NOT because 250 is known-sufficient.
  REMINDER (Eric): revisit ~2026-09-08 — if the dashboard shows usage
  well off the ceiling AND the news_scanner logs show the fmp count
  regularly hitting the 250 cap (truncation), raise the limit back
  (500 or 1000) via Railway env; if the cap never binds, leave it.
  Eric's standing position (2026-08-23): he wants the system as
  in-depth as possible and is OPEN to upgrading the FMP plan if the
  data shows depth is being lost — cost is not the constraint, waste
  was. Never let the quota silently cap coverage; surface the
  trade-off and let him choose.
  The heavy one-time history backfills ran on POLYGON, not FMP — the
  FMP burn was the news loop, so restoring the firehose is a data-
  quality question, never a backfill hangover.

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
