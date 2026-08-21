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
