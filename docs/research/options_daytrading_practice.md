# How effective options day traders operate — a knowledge-base note

Compiled 2026-09-03 at Eric's request ("deep research how the most
effective day trading options traders in the world operate"). Purpose:
give the desk's studies a practitioner frame to grade against, not to
import rules by authority. Every rule below that can be graded on the
stored record is listed as a hypothesis with the study that grades it.

Honesty first: the loud voices in this space are survivors. The
population evidence says the base rate is brutal, so "what the best do"
has to be separated from "what the best say", and both from "what the
record shows".

## 1. The population evidence (read before any rule)

- **Taiwan, 15 years, ~450k day traders/yr** (Barber, Lee, Liu, Odean):
  fewer than 1% are predictably profitable net of fees; roughly 3–5% are
  profitable in a given year; the predictably profitable group generates
  <10% of day-trading volume, and their edge is modest relative to capital
  and effort. Persistence exists but is rare and small.
- **Brazil, index-futures day traders** (Chague, De-Losso, Giovannetti):
  of those who kept going >300 sessions, 97% lost money; 1.1% earned more
  than minimum wage.
- **U.S. retail options** (Bryzgalova, Pavlova, Sikorskaya, J. Finance
  2023): retail prefers cheap weekly options with an average bid-ask
  spread of ~12.6% and loses money on average; losses come from spreads,
  time decay, and adverse selection. A related anatomy paper
  (Bogousslavsky & Muravyev) reaches the same aggregate conclusion.
- **0DTE**: by 2026 ~63% of SPX volume is same-day expiry; retail is ~54%
  of that flow (Cboe, 2025). The decay curve is gradual in the morning,
  accelerates after midday, and collapses in the last 90 minutes — an ATM
  SPX 0DTE loses on the order of $0.15–0.25/hour early, $0.40–0.55 midday,
  $0.80–1.20/hour in the final 90 minutes; the last two hours can burn
  30–40% of remaining value per hour regardless of spot.

Implication for the desk: the graded edge has to beat a spread that is
often 5–12% of premium on the contracts retail favors, plus a decay that
is back-loaded into the afternoon. Both are consistent with what the
desk's own option model found on the GO population (the average day's
move is smaller than the day's drag).

## 2. What the practitioner literature converges on

Across prop-desk material (SMB Capital / Bellafiore's *Playbook*),
options-specific guides, and the scaling-out literature, the same
process rules recur. They are listed as claims, not facts.

**Selection and time of day**
- Trade a small set of documented setups ("playbook") with known
  expectancy; review daily; cut what does not grade.
- The morning move is information-driven and tends to continue; the
  afternoon is liquidity/inventory-driven and mean-reverts more. Academic
  support: Gao–Han–Li–Zhou market intraday momentum (the first half-hour
  return predicts the last half-hour, strongest on volatile / high-volume
  / macro days); Da–Goyenko–Zhang on option returns (morning momentum =
  under-reaction to volatility shocks; afternoon momentum = market-maker
  inventory; 4pm returns revert next morning).
- Many desks avoid new entries in the 12:00–14:00 compression; 0DTE
  practitioners describe the 10:00–12:00 window as where the day's
  direction forms (a cited claim: the direction set by 10:30 holds into
  early afternoon on ~70% of days — unverified, grade it).

**Instrument**
- Day traders favor 0.45–0.70 delta; 0.60–0.70 (ITM) to minimize IV
  crush and reduce theta share; ATM for maximum dollar response. Deep ITM
  trades more like stock and pays less decay.
- Weeklies are the retail default and the widest-spread product; the
  spread is the first cost to beat.

**Exits — the core of Eric's question**
- Take profit into strength at pre-defined levels (prior-day high,
  opening-range high, high of day, prior swing highs, VWAP bands, round
  strikes / gamma walls); do not wait for a trailing close on a decaying
  instrument.
- Scale out: common plans are 50% at 1R and trail the rest, 40/30/30 at
  three targets, or 50% at 1R + 50% at 2R; after the first partial, move
  the stop to entry so the remainder is a "free trade" (the runner).
- Time stop: if the trade has not worked in 30–60 minutes, close it;
  "needs more time" in options usually means the thesis was wrong.
- Hard initial stop, set at entry, never widened; the daily loss limit
  (2–4% of the account, or 2–3× the per-trade risk) ends the day.

**What the backtest literature says about those exits**
- Scaling out raises the green-trade fraction and softens reversals but
  caps the largest winners; net expectancy can go either way and depends
  on how often price extends beyond the first target. Every partial pays
  its own spread and commission.
- Moving the whole position to breakeven early is repeatedly found to
  cut expectancy (Quantified Strategies; Van Tharp's framing: protecting
  pennies against the big move). This is the family the desk's own
  `be_1r` variant belongs to (17% win) — a FULL-position breakeven with
  nothing banked. Eric's rule is different: half banked at resistance,
  then breakeven on the remainder. It is not the same distribution and
  grades on its own.

## 3. What this changes in the desk's grading frame

The desk's intraday exits so far (hold to the bell, wide disaster line,
trail after +1R) were a swing playbook shrunk to one day. The give-back
read on the GO population (half reach +1%, a third of the +1–2% cohort
still closes red, 35% keep half their best gain) says the money is made
at the intraday high, not at the close. For a decaying instrument that
is decisive: the option frame, not the stock frame, picks the exit.

Gradeable hypotheses extracted from the above, all pre-registered in
the exit-shape study (task: "Tonight: exit-shape study") and the
day-state grid:

1. **Resistance targets.** Primary family (Eric, 2026-09-03: "our
   actual resistance lines where the stock has touched multiple times
   and been rejected"): the desk's multi-touch shelves from
   `analysis/levels.py` — fractal pivots on 1D/4H/1H/15m/5m clustered
   within an ATR-scaled band, ≥2 touches or multi-timeframe confluence,
   star-rated — recomputed AS OF each trade date from stored daily +
   1m-resampled bars, no lookahead. Secondary family for comparison:
   PDH, opening-range high, HOD-at-entry, round strikes; call wall from
   2026-07-15 only. Rules: full exit at TP1 touch; half at TP1 +
   breakeven on the rest (touch and 5m-close flavors); TP1 then TP2 at
   the next shelf; and the runner's stop RATCHETED up under each new
   higher low / behind each cleared level ("as runners move up we can
   move our SL up").
2. **Fixed R targets**: +50/+100/+150/+200 bps first touch; half at +100
   then trail.
3. **Time stop**: exit if not +X by 30 / 60 minutes after entry.
4. **Time exits**: 11:00 / 12:00 / 14:00, and "no new entries after
   12:00" as a population cut; last-90-minute decay makes a 14:00 exit a
   live candidate for the option frame.
5. **Morning-momentum conditioning**: first-half-hour direction and size
   as a day-state (Gao et al.), in the day-state grid.
6. **Instrument**: 0.70Δ vs ATM (0.55Δ) vs 0.85Δ in the option model,
   same exits, to price theta share against dollar response.
7. **Premarket high / overnight range** as a target: a HOLE until the
   premarket 1m backfill lands (RTH-only record today).

Costs are stated wherever numbers surface: the option model carries no
spread (declared hole); partial exits pay the spread twice; Polygon 1m
closes are fills.

## 4. Sources

- Barber, Lee, Liu, Odean — "Do Individual Day Traders Make Money?
  Evidence from Taiwan" (Haas working paper); Barber et al. "Day Traders
  Lose Money and Keep Trading" (Taiwan, 2020).
- Chague, De-Losso, Giovannetti — Brazilian day traders (summarized in
  multiple secondary reviews).
- Bryzgalova, Pavlova, Sikorskaya — "Retail Trading in Options and the
  Rise of the Big Three Wholesalers", Journal of Finance 78(6), 2023.
- Bogousslavsky & Muravyev — "An Anatomy of Retail Option Trading"
  (working paper, 2025).
- Gao, Han, Li, Zhou — "Market Intraday Momentum", Journal of Financial
  Economics 2018 (SSRN 2440866 / 2552752).
- Da, Goyenko, Zhang — "Intraday Option Return: A Tale of Two Momentum"
  (SSRN 5018430).
- Cboe statements on 0DTE share and retail participation (2025–2026);
  Option Alpha 0DTE decay research; SpotGamma / Volatility Box / marketxls
  0DTE decay guides; Harbourfront Quant "Retail Participation in the 0DTE
  Options Market" (2026).
- Bellafiore, *The Playbook* (SMB Capital); SMB Training "How to Day
  Trade"; LuxAlgo, Traders' Second Brain, Metriclan, QuantStrategy.io,
  Quantified Strategies on scaling out and breakeven stops; fattail.ai
  options day-trading guide (delta, time stop, sizing); prop-firm rule
  guides (daily loss limits, risk per trade).

Retrieved 2026-09-03 via web search summaries; several primary pages were
not fetchable from this environment, so figures above are as reported in
those summaries and should be re-verified before they are quoted as
primary.
