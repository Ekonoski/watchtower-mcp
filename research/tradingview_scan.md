# TradingView community indicator scan — candidates list

**Scan date:** 2026-09-01
**Sources:** tradingview.com/scripts/editors-picks/ (pages 1–2) and the
/scripts/ popular listing, as rendered that day. (A /scripts/top/ URL turned
out to be a keyword listing for "top", not a popularity sort — excluded.)
**Method:** mechanisms paraphrased from each script's public description and
open-source Pine source. No code copied. Author names and URLs kept for
attribution and re-checking.

**Doctrine:** every entry below is a CANDIDATE and nothing more. No mechanism
here carries a trading claim until it is pre-registered, graded on our own
stored record (era split + replication bar), and read out. This file answers
one question per script: *could our stored 1m/15m record grade it at all?*

## What the stored record can grade (reference for the verdicts)

- `index_intraday_bars`: SPY 15m RTH 2005→present, QQQ from 2011 (IWM holey,
  excluded). Owned by the 16:20 cron. Grades anything computable from
  15m OHLCV on the indexes, 20+ years, era-splittable.
- 1m bars, 11 liquid names, ~2 years (the frequency-night corpus; research
  re-fetch is legitimate for studies — reconstruction-is-not-tape governs
  live grading only). Grades 1m mechanisms and 1m-intrabar reads inside
  15m candles, on that universe only.
- `paper_spec_bars`: 15m with volume (volume nullable on legacy rows),
  armed-spec universe, recent history only.
- `daily_prices` fleet + oscillator/pattern scans for anything daily+.
- NOT available: tick/bid-ask data, sub-minute intrabars, stored intraday
  VIX or other external-symbol intraday series, order-book anything.
  "Volume delta" is therefore always an approximation here (1m candle
  direction as the proxy), never true buy/sell attribution — any grade of a
  delta mechanism must say so where the numbers surface.

Verdict key: **GRADEABLE** (a pre-registered study could run on stored data
as-is) · **PARTIAL** (gradeable only as a coarse proxy or on a limited
universe — the limitation stated) · **NOT GRADEABLE** (needs data we don't
have) · **TOOLING** (no trading mechanism to grade).

---

## Editors' Picks

### 1. Volume Delta Footprint Map — Zeiierman
`/script/7vcb6M4J-Volume-Delta-Footprint-Map-Zeiierman/` · open-source
**Mechanism:** splits each chart candle's price range into horizontal
stripes; lower-timeframe candles inside it contribute +volume if bullish,
−volume if bearish, distributed across the price levels they traded through.
Accumulated per-stripe delta decays exponentially; stripes are ranked by
delta magnitude and dominance; optionally a stripe extinguishes once price
trades back through it. Pure visualization — no entry/exit conditions.
**Data:** LTF intrabar OHLCV (`request.security_lower_tf`).
**Verdict: PARTIAL.** We can rebuild the coarse version (1m intrabars inside
15m candles, candle-direction delta proxy) on the 11-name 1m corpus. But
there is no discrete signal to grade — a study would first have to invent
one (e.g. "reaction at the top-ranked untouched stripe"), which makes it a
level-zone candidate in the FVG family, not an entry candidate.

### 2. Initial Balance Auction Intelligence — dgtrd
`/script/ks2QGulb-Initial-Balance-Auction-Intelligence-by-DGT/` · open-source
**Mechanism:** builds the Initial Balance (high/low/mid of a configurable
opening window), then classifies successive post-IB windows against that
range into discrete auction states (probing / accepted / failed /
continuation / rejection / two-sided), rolls those up into a session regime
(balanced → trend auction), a lifecycle phase, a bounded ±100 pressure
score, and a conviction grade from close strength + extension + retest
behavior. Alerts fire on confirmed state transitions at window completion.
**Data:** chart OHLCV + 1m intrabars for window precision; intraday ≤30m.
**Verdict: GRADEABLE — priority.** IB range and window-close state
transitions are computable from SPY/QQQ 15m alone, 20 years, era-splittable;
1m refinement available on the 11 names. This is the same terrain as the
day-bias study (prior-range acceptance, retest timing) with a different
vocabulary — the natural pre-registered question is whether IB-state
transitions add anything beyond the open-above-PDH read we already graded.

### 3. BYO Pattern V1 — Trendoscope
`/script/e2S2v4fl-BYO-Pattern-V1-Trendoscope/` · open-source
**Mechanism:** zigzag pivots (sensitivity-parameterized), takes the last 5–6
alternating pivots, computes Fibonacci retracement/extension ratios between
legs, and flags a pattern when user-enabled ratio ranges all validate; draws
a potential-reversal zone. It is a pattern *construction kit* (harmonics:
Gartley/Bat/etc. are configurations), not one pattern.
**Data:** OHLCV only, any timeframe.
**Verdict: GRADEABLE, low priority.** OHLCV-only, so any stored timeframe
works — but "a family of user-defined ratio patterns" is a large
pre-registration surface (each configuration is its own class, per the
flat-score-bar lesson). Only worth a study if a specific named harmonic is
nominated first.

### 4. TASC 2026.09 Adaptive SuperSmoother — PineCodersTASC
`/script/FnlMn99W-TASC-2026-09-Adaptive-SuperSmoother/` · open-source
**Mechanism:** a fixed-period second-order SuperSmoother filter beside an
adaptive one whose period shrinks when momentum (RMS-normalized one-bar ROC
of the fixed filter) rises. Declared rule: long while adaptive sits above
fixed, short otherwise. Close price only.
**Data:** close only.
**Verdict: GRADEABLE, cheap.** Computable on any stored series. Note it is a
symmetric always-in-the-market trend rule, and the short half starts from a
desk posture where no graded short mechanism exists — grade both sides,
expect to keep at most the long filter reading.

### 5. LTF Volume Microburst Bubbles — Zeiierman
`/script/Hdskv6Q5-LTF-Volume-Microburst-Bubbles-Zeiierman/` · open-source
**Mechanism:** inside each chart candle, flags lower-timeframe candles whose
volume spikes above an EMA baseline AND whose body/range ratio shows
directional efficiency (filters out high-volume wicks); nets qualifying
bullish vs bearish bursts into a score; signal when the score crosses a
threshold.
**Data:** LTF intrabar OHLCV + volume.
**Verdict: PARTIAL.** Directly buildable at 15m-with-1m-intrabars on the
11-name/2-year corpus (volume is in the 1m bars). Universe-limited, and it
is close kin to the defense-shadow's "buyers defending on relative volume"
read — the honest study is whether burst-score adds to `find_defense`, not a
standalone entry.

### 6. Regression_Toolkit — Steversteves
`/script/gSLL5PC1-Regression-Toolkit/` · open-source library
**Mechanism:** a Pine library of regression fits (multiple/ridge/lasso/
elastic-net/logistic/quantile/Huber) with diagnostics. No signal logic.
**Verdict: TOOLING.** Nothing to grade; noted for completeness.

### 7. Time-of-Day/Session Performance Stats — QuantAlgo
`/script/Yr3kT0uI-Time-of-Day-Session-Performance-Stats-QuantAlgo/` · open-source
**Mechanism:** ranks hours of day by average range, volume, close-direction
rate, and open-to-close drift over a trailing window; composites them; ranks
the four global sessions; shades the chart and proposes "focus hours."
Measurement, not entries.
**Data:** intraday OHLCV.
**Verdict: GRADEABLE — and largely already done.** This is the machinery the
day-bias and tape-entry studies already run (the 10:30 boundary IS a
time-of-day conditioning result). The one un-asked question it suggests: a
rolling per-name hour-of-day conditioning on the 11-name 1m corpus, as a
FILTER tag on RS-leader days. Candidate tag, never a gate until graded.

### 8. Universal Signal Backtester — LuxAlgo
`/script/Y5CIZ9CB-Universal-Signal-Backtester-LuxAlgo/` · open-source
**Mechanism:** an on-chart backtest harness — plug in MA crosses or an
external signal, simulate up to 3 TP / 3 SL levels, report win rate, profit
factor, equity curve, hour/day heatmaps, with spread/commission presets.
**Verdict: TOOLING.** We have a harness doctrine and it is stricter than
this (era split, replication, capped outliers, no costs stated as caveat vs
preset costs). Nothing to adopt.

### 9. MACD with HTF Panels — TheUltimator5
`/script/pUwmpOyc-MACD-with-HTF-Panels-theUltimator5/` · open-source
**Mechanism:** standard MACD with slope/sign histogram coloring plus up to
four higher-timeframe MACD mini-panels and a cross-timeframe value table.
Display innovation only; the computation is stock MACD.
**Verdict: GRADEABLE but redundant.** MACD is already the oscillator
suite's confirmation layer and its components are already graded/tagged
(osc_state). The multi-TF *alignment* idea is the only new question, and
the cipher tag already carries the per-timeframe components to answer it.

### 10. Whale Liquidity and Absorption Profile — AlgoAlpha
`/script/cWm8UcfQ-Whale-Liquidity-and-Absorption-Profile-AlgoAlpha/` · open-source
**Mechanism:** samples LTF volume inside each candle into horizontal price
bins; builds a strong/weak volume profile (percentile thresholds), a per-bin
delta heatmap, and an "absorption" profile — bullish volume landing in upper
wicks / bearish in lower wicks — whose local peaks project forward as
support/resistance reaction zones; extreme intrabar events marked on price.
**Data:** LTF intrabar OHLCV + volume.
**Verdict: PARTIAL.** The absorption-zone projection is the gradeable core:
build zones from 1m-inside-15m on the 11-name corpus, grade forward
reactions exactly as the FVG zones are recorded (a run row per sweep, zero
zones is a quiet read). Delta remains a candle-direction proxy — stated
wherever a grade surfaces.

### 11. Fractional EMA Kalman Filter [D7] — et20tradeview
`/script/c75aF3t1-Fractional-EMA-Kalman-Filter-D7/` · open-source
**Mechanism:** a sub-integer-period double EMA (deliberately oscillatory,
amplifying regime shifts) feeds a one-state Kalman filter whose process and
measurement noise self-tune from ATR-normalized residual variance over short
and long windows; a sensitivity floor prevents overconfidence in quiet tape.
Two filter speeds plotted; no discrete entries.
**Data:** OHLCV (close + ATR).
**Verdict: GRADEABLE as a filter/trail candidate.** The concrete question
our record can answer: does this filter's line beat the 21-EMA as the
after-+1R trail on the already-graded RS-leader entries (hybridexit-style
re-simulation, same entries, exits swapped)? That study design already
exists; the filter just becomes one more exit variant.

### 12. Neural Weight Oscillator — Zeiierman
`/script/bfu1hmkS-Neural-Weight-Oscillator-Zeiierman/` · open-source
**Mechanism:** three component models (trend: fast/slow EMA + slope; mean
reversion: RSI exhaustion + deviation from mean; momentum: ROC + RSI
momentum + EMA velocity), weighted by a user's best/worst designation, with
an "adaptive training" layer that amplifies whichever features preceded the
strongest historical reactions; output normalized 0–100 with fixed
overbought/oversold thresholds.
**Data:** OHLCV.
**Verdict: GRADEABLE, doctrine-flagged.** Computable — but it is a blended
0–100 composite, the exact family the desk refused as a gate (the confluence
score sign-flips across timeframes; components carry the signal). The
self-training layer also learns in-sample on the displayed chart. If graded
at all, grade the raw components, which we largely already have.

## Popular listing

### 13. Dynamic Grid Indicator — BigBeluga
`/script/WKSKrMsu-Dynamic-Grid-Indicator-BigBeluga/` · open-source
**Mechanism:** normalizes price deviation from a Hull MA baseline in ATR
units; projects up to 5 ATR-stepped grid levels each side; a companion
oscillator plots the normalized deviation; directional signals at grid
boundary crossovers.
**Data:** OHLCV.
**Verdict: GRADEABLE.** ATR-normalized deviation-from-baseline crossings
are computable on the 15m index record and the 1m corpus. It is a
band-crossing continuation rule — the study question is whether it differs
from the (refused) unconditioned band/breakout families once era-split.

### 14. Dynamic Deviation Channels (RSI Trigger) — ChartPrime
`/script/sSqHpGiw-Dynamic-Deviation-Channels-RSI-Trigger-ChartPrime/` · open-source
**Mechanism:** configurable MA midline with three ATR-multiple deviation
tiers; a smoothed RSI gates which side renders (upper bands when RSI ≥ 50,
lower when < 50); entry triangles print when price touches the primary band,
with a spacing guard against clustered signals.
**Data:** OHLCV.
**Verdict: GRADEABLE.** Fully computable everywhere. Mechanically it is
"buy the pullback band touch while momentum is bullish" — adjacent to the
RS-leader pullback definition; a grade should include the leader-day join,
since the frequency night showed unconditioned entries on these names are
coin flips and selection carries the edge.

### 15. Zeiierman Bands — Zeiierman
`/script/YtNcfY4g-Zeiierman-Bands-Zeiierman/` · open-source
**Mechanism:** a liquidity-weighted mean (price weighted by volume
participation and candle range, wick behavior adjusting contributions) with
asymmetric bands that expand independently per side from directional
dispersion/wick/liquidity stress; a color engine from higher-timeframe
position + path efficiency; and *reclaim* signals — armed when price tags an
outer band, firing when price reclaims back toward the mean, optionally
filtered by an Ornstein-Uhlenbeck mean-reversion test and trend regime.
**Data:** OHLCV + volume, plus one HTF reference.
**Verdict: GRADEABLE — interesting shape.** Arm-at-extreme / fire-on-reclaim
is structurally our washout-reversal family (and the wick-aware mean echoes
the wick rule). Computable on 15m indexes and 1m names. The OU-filter leg is
a genuinely new conditioning idea our record can test cheaply.

### 16. VWAP Delta — graefe
`/script/94mLUhIV-VWAP-Delta/` · open-source
**Mechanism:** re-plots each bar's OHLC as distances from session VWAP
instead of absolute price, compares that delta series to its own EMA
baseline (optional Hull smoothing); bias read from delta vs baseline.
Explicitly intraday (session-anchored VWAP).
**Data:** intraday OHLCV + volume.
**Verdict: GRADEABLE.** Session VWAP is computable from stored 1m/15m bars
with volume. The gradeable question: does VWAP-relative position/cross add
to the leader-day or day-bias conditioning we already have.

### 17. Coppock Curve Multi-Filter — MarkitTick
`/script/Ua5pRxsd-Coppock-Curve-Multi-Filter-MarkitTick/` · open-source
**Mechanism:** classic Coppock (sum of two ROCs, WMA-smoothed) with
directional-cross and zero-cross signals, seven toggleable confirmation
filters (ADX floor, divergence suppression, slope acceleration, volume,
HTF alignment, volatility-adjusted zero band, signal persistence), optional
pre-smoothing of the source by eight methods, and ATR-derived stop plus
three R-multiple take-profits per signal.
**Data:** OHLCV.
**Verdict: GRADEABLE, low priority.** The core cross is computable anywhere;
the seven-filter × eight-smoother surface is an overfit machine (every
toggle is a researcher degree of freedom). If nominated, pre-register the
bare Coppock cross only. Its bracket exits are the family the 1m record
already graded and refused (the 2R bracket; breakeven-after-1R the trap).

### 18. Volatility Regime Tracker — Nick_Joan
`/script/YAB6xPIY-Volatility-Regime-Tracker-NickJoan/` · open-source
**Mechanism:** realized volatility as stdev/SMA of price over a window,
percentile-ranked against its own history; LOW/NEUTRAL/HIGH regimes at the
30th/70th percentiles; a volatility-MA agreement check strengthens or grays
the state; per-regime duration statistics (mean ± SD of how long regimes
last) rendered against the current run; alerts on regime change.
**Data:** OHLCV.
**Verdict: GRADEABLE as a conditioning tag.** Trivially computable. Not an
entry — a candidate regime tag in the osc_state/sector_state pattern
(stamped after curation, arming-blind, graded on the book's own
resolutions). The duration-statistics idea (is this regime old?) is the
novel bit.

### 19. VWAP AI — Statistical Bands & Touch Stats — Dots3Red
`/script/Vs7khoJl-VWAP-AI-Statistical-Bands-Touch-Stats-Dots3Red/` · open-source
**Mechanism:** session VWAP with two standard-deviation band tiers; every
band interaction is graded on confirmed bars into rejection / break /
timeout, plus break-then-revert tracking, and the chart displays running
per-band hold/break percentages *with n beside them*.
**Data:** intraday OHLCV + volume; 1m–1h intended.
**Verdict: GRADEABLE — priority.** This is methodologically our house style
(grade the level's own record, print n) applied to VWAP bands. The stored
15m index record can produce 20 years of band-touch outcome tables,
era-split, and the result would slot beside the levels/day-bias reads as a
"does the ±1σ band actually hold, and when" table. Cheapest high-value
study on this list.

### 20. Split VWAP — alodis
`/script/v2BhTtjK-Split-VWAP/` · open-source
**Mechanism:** renders each candle as two partial candles bisected at
session VWAP, volume apportioned by segment height, colored by same-side
volume and price change vs the prior bar. Pure visualization.
**Verdict: TOOLING/RENDER.** No mechanism to grade.

### 21. ATR Delta — graefe
`/script/AZdx8UK2-ATR-Delta/` · open-source
**Mechanism:** splits true range by bar direction (close up vs down),
RMA-smooths each side separately, and plots up-side volatility, down-side
volatility (mirrored), and their midpoint delta — a directional
decomposition of ATR.
**Data:** OHLCV.
**Verdict: GRADEABLE as a conditioning read.** Computable everywhere. Not an
entry; a candidate regime/filter component (directional volatility dominance
as a tag). Would need to show it adds beyond the components already tagged.

---

## Shortlist (gradeability × novelty, still only candidates)

1. **VWAP band touch statistics** (#19, with #16 as a cousin) — house-style
   level grading on a level family we don't currently grade; runs on the
   full 15m index record today.
2. **Initial Balance auction states** (#2) — 20-year era-splittable study on
   stored index bars; must be graded *against* the existing day-bias read,
   not beside it.
3. **Zeiierman Bands' arm/reclaim structure + OU filter** (#15) — the
   reclaim shape matches doctrine the desk already trades; the OU
   mean-reversion gate is the new, cheap-to-test leg.
4. **Kalman filter as an exit-trail variant** (#11) — drops straight into
   the existing hybrid-exit re-simulation harness on already-graded entries.
5. **Absorption zones from intrabar volume** (#10, with #1/#5 as kin) —
   FVG-family zone grading on the 11-name 1m corpus; delta is a proxy and
   says so.

Non-candidates from this scan: #6, #8, #20 (tooling/rendering), #9
(redundant with the oscillator suite), #12 (refused-composite family +
in-sample training). Time-of-day (#7) is already substantially answered by
the day-bias and tape-entry studies.

Nothing above is armed, tagged, alerted, or traded by virtue of appearing
in this file. Each shortlisted item needs its own pre-registered spec —
event, condition, outcomes, baselines, era split, replication bar — before
anyone reads a number.

---

# Pass 2 — category-targeted scan

**Scan date:** 2026-09-01 (same day, second pass)
**Sources:** TradingView category listings as rendered that day:
`/scripts/volume/`, `/scripts/supportandresistance/`, `/scripts/volatility/`,
`/scripts/trailingstop/`. Popularity lists skipped by design.
**Method, verdict key, and doctrine:** identical to pass 1. Mechanisms
paraphrased from public descriptions/open-source Pine; no code copied; no
trading claims; everything is a candidate until pre-registered and graded.
Scripts already covered in pass 1, or mechanically redundant with a covered
entry, are noted with what they duplicate instead of a full write-up.

One data hole, recorded as a hole: **Sweep Reversal Map [Herman]**
(`/script/vPAohL1S-...`, support/resistance listing) returned HTTP 404 at
fetch time — unreachable, not assessed.

## Category 1: Volume / order-flow

### P2-1. Regime Gated Confluence Score — Pineify
`/script/m99Sdj2H-Regime-Gated-Confluence-Score-Pineify/` · open-source
**Mechanism:** three normalized factors — trend (ATR-normalized EMA spread +
slope), momentum (centered RSI, inverted in range regime), volume (close
position × relative volume, dropped if volume coverage < 80%) — blended
under regime-dependent weights. Regime is classified into TREND / RANGE /
VOLATILE / TRANSITION with hysteresis, from EMA separation, path efficiency,
and ATR displacement. An agreement gate attenuates the score when components
disagree.
**Data:** OHLCV.
**Verdict: GRADEABLE, doctrine-flagged.** A blended composite — the family
the desk refused as a gate (components carry signal; composites sign-flip).
The one novel leg is regime-dependent *weighting with hysteresis*; if
anything is nominated, it is the regime classifier alone, as a tag.

### P2-2. Volume Surge Radar 2x/4x/8x/16x — sunilp303
`/script/HR7VSSAZ-Volume-Surge-Radar-2x-4x-8x-16x/` · open-source
**Mechanism:** volume baseline ≈ one trading week (timeframe-adaptive);
each bar classified into 2×/4×/8×/16× tiers vs that baseline; a rolling
window counts repeat qualifying bars; a bias label (RISING/FALLING/MIXED/
QUIET) combines window price direction with the up/down composition of the
surge bars.
**Data:** OHLCV.
**Verdict: GRADEABLE.** Tiered-RVOL-with-repetition is computable on any
stored series. Candidate screen/conditioning read in the relative-volume
family (see P2-5, the stronger formulation).

### P2-3. Contested Volume Bubbles — B3AR_Trades
`/script/6Pw3RCO2-Contested-Volume-Bubbles/` · open-source
**Mechanism:** "contested volume" = min(buy, sell) volume per bar,
equivalently (total − |delta|)/2, localized by sampling up to 20 LTF
segments per candle; bubbles print where contested volume exceeds a
percentile threshold, sized vs the prior 100 bars; a time-of-day mode
normalizes against typical volume for that clock slot.
**Data:** OHLCV + LTF intrabars for precision.
**Verdict: PARTIAL.** Buildable with the candle-direction delta proxy on
the 11-name 1m corpus (stated as a proxy wherever graded). The
battleground-detection idea is kin to the gamma doctrine's
both-walls-one-strike magnet read; the time-of-day volume normalization is
the P2-5 idea and is the gradeable part.

### P2-4. Structure Participation Matrix — MQLSoftware
`/script/OUrrKkjm-Structure-Participation-Matrix-MQLSoftware/` · open-source
**Mechanism:** strict symmetric pivots; a structure break confirms only on a
close beyond the armed level plus an ATR buffer. At the break close, four
0–100 components FREEZE — path efficiency of the leg, average directional
close location, leg volume vs rolling median, volume weighted by close
location — into a composite band (LOW→VERY HIGH). Exactly N bars later an
outcome check records HELD or FAILED. Unscored events (missing volume,
bounded history) are excluded from the statistics explicitly.
**Data:** OHLCV.
**Verdict: GRADEABLE — priority.** This is house methodology in indicator
form: freeze the evidence at event time, grade the event's own outcome,
exclude holes loudly. The pre-registerable question: do these four
break-quality components, frozen at our pattern episodes' breakout closes,
sort the retest outcomes the desk actually trades? Runs on
pattern_backtest + stored bars.

### P2-5. Intraday Relative Volume+ — cmacktrades
`/script/f1WSLv7z-Intraday-Relative-Volume/` · open-source
**Mechanism:** each intraday bar's volume is judged against historical
volume *from the same point in the session* (not a generic rolling mean),
so the open/midday/close volume shape is the denominator; the current
session is excluded from its own baseline; a conventional rolling average
runs beside it for contrast. Minute charts only.
**Data:** intraday OHLCV.
**Verdict: GRADEABLE — priority.** Time-of-day-matched RVOL is the correct
denominator our defense detector currently approximates with a pullback-
local average. Candidate refinement to `find_defense`'s volume baseline —
which is a detector-definition change and therefore a GRADED change, per
the defense-premium-cap rule; never swapped in by feel. (The author's
Daily Relative Volume+ is the same idea at daily scale — redundant,
not separately assessed.)

### P2-6. Smart Entry Zones — StrixEDGE
`/script/iemKPzGk-Smart-Entry-Zones-StrixEDGE/` · open-source
**Mechanism:** Supertrend flip generates the setup (entry, three TPs, stop);
Fisher Transform cross at extremes and Chaikin Money Flow sign act as
confirmation votes, with a 1/3–3/3 alignment counter; live hit-marking on
targets and stops.
**Data:** OHLCV.
**Verdict: GRADEABLE, low priority.** A confluence-counter mashup over a
stock Supertrend flip; the TP-ladder expression is the bracket family the
1m record already graded and refused. Nothing novel beyond its parts.

### P2-7. CandelaCharts — Value Area Reversals
`/script/korzg0xR-CandelaCharts-Value-Area-Reversals/` · open-source
**Mechanism:** rolling session volume profile; the 70% value area's
boundaries are tracked; a reversal fires when price exits the value area
and then reclaims it with anomalous volume (volume compared against the
last N similar candles); the reversal leg's swing point anchors a projected
level until price confirms the shift; alerts on reclaims and level breaks.
**Data:** OHLCV.
**Verdict: PARTIAL/GRADEABLE.** Volume-at-price from 15m/1m bars is an
approximation (bar volume spread over bar range), stated where graded. The
exit-then-reclaim-with-volume structure is our washout-reclaim + defense
family at the value-area level — the gradeable question is whether VA
boundaries out-perform the level families we already grade, not whether
reclaims work (that read exists).

### P2-8. HTF Auction Candle — Zeiierman
`/script/HhfasNyh-HTF-Auction-Candle-Zeiierman/` · open-source
**Mechanism:** reconstructs the forming higher-timeframe candle and maps
LTF activity inside it — per-price-cell buy/sell estimates (from close
position, direction, wick structure), delta dominance per cell, and the
highest-activity "control price." Pure visualization; no signals.
**Verdict: PARTIAL/TOOLING.** Delta-proxy render of the footprint family
(pass-1 #1); no discrete mechanism to grade.

**Redundant, noted per instruction:** Footprint Delta Auction Map
[BullByte] duplicates the footprint/delta-map family (pass-1 #1);
Volume + RVOL + Directional Delta [Clean] duplicates the RVOL family
(P2-5/P2-2) plus the candle-direction delta proxy; Custom Footprint
[Auto-Scale & Filter] (volatility listing) is the footprint family again.

## Category 2: Market structure / support-resistance

### P2-9. MarketMaulers Auto Trendlines — AlphaTraderRyan
`/script/rAp1tPZr-MarketMaulers-Auto-Trendlines/` · open-source
**Mechanism:** diagonal trendlines from confirmed pivot pairs (higher lows /
lower highs), promoted through an explicit lifecycle — Forming → Validated
(≥3 touches) → Broken → Retested/Failed — with a dual break test: distance
(0.35 ATR beyond the line) OR persistence (three consecutive closes on the
wrong side). A broken-then-held line flips polarity (support becomes
resistance) rather than retiring, with its flip count labeled. Also
projects parallel channel rails and computes convergence points of two
validated lines. HTF engine reads completed bars only.
**Data:** OHLCV.
**Verdict: GRADEABLE — priority.** Diagonal levels are a family the levels
engine doesn't currently grade (our shelves are horizontal). The lifecycle-
with-status design would pass our status-census audit by construction, and
the close-persistence break test is a wick-rule cousin. Pre-registerable:
touch/break/flip outcome tables on the 15m index record, graded beside the
horizontal-shelf record.

### P2-10. Multi-Timeframe Structure Overlay — itamardrori_
`/script/JeW9tUix-Multi-Timeframe-Structure-Overlay-ITA/` · open-source
**Mechanism:** swing highs/lows from two higher timeframes projected onto
the chart; Break of Structure (close beyond last swing, trend direction)
vs Change of Character (first close breaking against trend) labeled per
timeframe, with per-TF bias tags and an alignment alert. Lookahead off,
confirmed bars only.
**Data:** OHLCV.
**Verdict: GRADEABLE but largely redundant.** BOS/CHoCH is our
higher_low/lower_high structure vocabulary; CHoCH-as-first-warning is what
`bearish_conflicts` already stamps. The only open question — does HTF
alignment add to the daily/weekly joint gates — is answerable from scans
we already store.

### P2-11. Edo Premium Discount — EdoLab-Markets
`/script/ZgbC7wJt-Edo-Premium-Discount/` · open-source
**Mechanism:** dealing range from the last confirmed swing high/low; close
position expressed as 0–100% of that range; three zones (premium /
equilibrium / discount) with zone-entry alerts.
**Data:** OHLCV.
**Verdict: GRADEABLE, cheap tag.** Trivial to compute. Candidate
measurement tag ("where in the dealing range did the spec arm/fill") in the
osc_state pattern — arming-blind, graded on the book's own resolutions.

### P2-12. ICT Equal Highs & Lows Liquidity Pools — KronosMMXM
`/script/0qehowvK-ICT-Equal-Highs-Lows-EQH-EQL-Liquidity-Pools/` · open-source
**Mechanism:** clusters of near-equal swing highs/lows (tolerance in ATR,
ticks, or %) become visible pools only after a second qualifying swing;
each pool carries a three-state taxonomy — untouched / SWEPT (wick through,
close back inside) / BROKEN (close beyond) — and the script replays history
so an already-breached pool can never render untouched.
**Data:** OHLCV.
**Verdict: GRADEABLE — priority.** The swept-vs-broken distinction is the
wick rule expressed as a level-state taxonomy, and our multi-touch shelf
engine doesn't currently separate those outcomes. Pre-registerable on the
15m index record: forward outcomes after a SWEEP of a multi-touch level vs
after a close-through BREAK — era split, both tickers. Slots directly
beside the levels/day-bias reads.

### P2-13. Order Blocks Graded — itamardrori_
`/script/Q8SvycX7-Order-Blocks-Graded-ITA/` · open-source
**Mechanism:** the last opposite-color candle before a move that broke a
prior swing point becomes a zone; graded A/B/C by impulse distance in ATR
multiples and origin-candle volume vs average; zones grey out or retire on
price re-entry.
**Data:** OHLCV.
**Verdict: GRADEABLE.** A zone family with an explicit admission rule
(structure break required) and a grading axis — mechanically close to how
`fvg_zones` records displacement-quality zones. Candidate: grade order-block
reactions with the same run-row-per-sweep machinery, so absence stays
unambiguous.

### P2-14. Absorption Shelf Dwell-Coil Reversal Levels — Market_Logic_India
`/script/HWn4gTdW-Absorption-Shelf-Dwell-Coil-Reversal-Levels/` · open-source
**Mechanism:** a rolling occupancy-time map — how long price DWELLS at each
level — generates candidate bands (percentile-hot levels merged); a band
activates as a shelf when volatility compresses (fast/slow ATR ratio) while
closes stay contained. Five confirmation lenses grade active shelves
(effort-vs-result, price-impact collapse, dwell decay, rejection-wick
clustering, cumulative-delta divergence), fused via correlation-aware
Bayesian log-odds into BALANCE/ABSORBING/PRIMED; a confirmed close exiting
the band flips the signal. Ships its own forward-test calibration:
rejection rate vs matched base rate, in/out-of-sample split, Wilson 95%
lower bound. Lenses abstain (not fake) when volume is missing.
**Data:** OHLCV; volume optional; LTF delta optional.
**Verdict: PARTIAL — but the dwell idea is GRADEABLE and novel.** The full
five-lens fusion needs the delta proxy and is a big surface. The core leg —
time-at-price occupancy as the level generator, instead of volume-at-price
or touch counts — is computable from bare 15m bars, 20 years, and is a
genuinely different definition of "shelf" than the levels engine uses.
Methodological kinship noted for the record: base-rate matching, IS/OOS
split, Wilson bounds, and abstain-on-missing-data are our own rules.

### P2-15. Darvas Box Ladder — itamardrori_
`/script/6kCBXCdW-Darvas-Box-Ladder-ITA/` · open-source (also in the
trailing-stop listing)
**Mechanism:** a box ceiling forms when a new high survives N bars
unbeaten; the floor is the lowest low since that ceiling, also
confirmation-tested; completed boxes stack into a ladder; breakout =
close above ceiling with volume above the 20-bar average; stop under the
active floor; measured-move target one box height above.
**Data:** OHLCV; daily uptrends intended (new-high filter).
**Verdict: GRADEABLE, mostly redundant.** This is the range_breakout
lifecycle with a stricter formation rule. The one nominable leg: the
"ceiling must survive N bars unbeaten" formation test as an alternative
admission rule for the range class — a class-definition change, so it
waits on a full re-grade like the CIFR neckline constraint.

### P2-16. Reaction Weighted Support Resistance — Pineify
`/script/mi7UPDe5-Reaction-Weighted-Support-Resistance-Pineify/` · open-source
**Mechanism:** confirmed-pivot zones with ATR-scaled width frozen at pivot
time; same-side zones merge under a height cap with score-weighted centers;
each completed touch (after a cooldown) is scored by the best favorable
excursion it produced, ATR-normalized, volume-weighted; scores decay by
half-life; a close beyond zone + ATR buffer invalidates.
**Data:** OHLCV (+ volume optional, neutral fallback).
**Verdict: GRADEABLE — priority.** Levels weighted by their own measured
reactions, with decay and explicit invalidation — the house's
grade-the-level's-own-record style as a scoring rule. The pre-registerable
question: does reaction-magnitude weighting rank our multi-touch shelves
better than the current touches × confluence × recency stars? Runs on the
existing levels data plus stored bars.

**Redundant/covered, noted per instruction:** Split VWAP and VWAP AI Touch
Stats — pass 1 (#20, #19). Pattern Atlas: Geometric [AxeAlgo] — geometric
pattern-class detection, duplicates our own pattern scanner plus the
pass-1 BYO kit (#3). FCP HL Levels (daily/weekly/monthly highs-lows) —
duplicates the PDH/PDL + levels machinery the desk already trades and
grades. FCP Market Sessions High-Low Box & Range Stats (also in the
volatility listing) — session range statistics, duplicates the time-of-day
family (pass-1 #7 / P2-19).

## Category 3: Volatility regime

### P2-17. VIX 3D Term Structure — MantisAlgo
`/script/iiGlchUQ-VIX-3D-Term-Structure-MantisAlgo/` · open-source
**Mechanism:** renders six CBOE vol indices (VIX1D/9D/VIX/3M/6M/1Y) as a
surface vs a historical baseline; classifies the curve as contango / flat /
backwardation from the back-minus-front spread (±0.35 threshold) and a
LOW/MID/HIGH vol level from the median of the middle tenors; alerts on
regime shifts.
**Data:** external symbols only (CBOE index series).
**Verdict: NOT GRADEABLE from the stored record.** We store no VIX term
structure. Surfaced rather than buried, per the FMP-budget doctrine: IF
term-structure state is ever wanted as a conditioning input, it is a data-
acquisition decision (start persisting the series, like the 5:35 IV
snapshot job) — Eric's call, not a backfill improvisation.

### P2-18. Market Regime: NQStats — lucymatos
`/script/90O8rh4b-Market-Regime-NQStats/` · open-source
**Mechanism:** rolling stdev of daily log returns (10/20/50d windows)
divided by a multi-year baseline stdev (default 10y); ratio >1 = elevated,
<1 = compressed; percentile rank and a BUILD/EASE/HOLD trend of the ratio.
Explicitly disclaims direction — "describes the character and intensity."
Always computes on the daily series regardless of chart.
**Data:** daily OHLCV.
**Verdict: GRADEABLE, cheap tag.** Same percentile-regime family as pass-1
#18 (Volatility Regime Tracker) — the differences are the multi-year
baseline and the daily-only discipline. One nominee from this family at
most; a candidate regime tag, arming-blind.

### P2-19. Daily High & Low Time Map (HOD/LOD) — KronosMMXM
`/script/u0etVs4Y-Daily-High-Low-Time-Map-HOD-LOD/` · open-source
**Mechanism:** across completed days, records which intraday time bucket
printed the day's high and which printed the low; renders frequency heat
rows plus a conditional dashboard — given the current hour, what fraction
of historical days still had their high ahead; filters by weekday, up/down
day, sample window; alerts on entering the modal bucket or when the
"high still ahead" probability crosses a threshold.
**Data:** intraday OHLCV.
**Verdict: GRADEABLE — priority.** Directly computable from the 15m index
record: 20 years of HOD/LOD timing, era-split, conditioned on the day-bias
states we already classify (open-above days, flat opens). Complements the
day-bias study — that work graded level touches; this asks WHEN extremes
print, which bears on the bell-exit and trail-after-1R conventions. Cheap
and pre-registerable in one sitting.

### P2-20. Turtle ATR Channel Breakout — IMTangYuan
`/script/E1OhgTQz/` · open-source
**Mechanism:** 350-day SMA midline with asymmetric bands at +7× and −3×
ATR(20); a signal fires on the first daily close through a band (no
duplicates until price re-enters and crosses again); exits selectable —
midline cross, 10-bar Donchian, or N-based trail with optional 2N hard
stop; filters for slope, HTF trend, ADX, channel expansion.
**Data:** daily OHLCV.
**Verdict: GRADEABLE, low-medium priority.** Classic daily breakout
machinery on daily_prices; overlaps the breakout families already graded at
episodes. The short side faces the standing bar: no graded short mechanism
exists and a new one needs era-stability + replication.

### P2-21. MAD Volatility Trail — BackQuant
`/script/H9JdI5rn-MAD-Volatility-Trail-BackQuant/` · open-source
**Mechanism:** rolling median of price with Median Absolute Deviation
(median of |distance from median|) as the dispersion measure — robust to
outlier bars — scaled into bands with an optional ATR floor against
compression; bands become one-sided ratcheting trails (lower trail can
only rise while price holds above; mirror for upper); regime flips on a
close through the opposite trail, optionally confirmed by median slope.
**Data:** OHLCV.
**Verdict: GRADEABLE — exit-variant candidate.** Drops straight into the
hybrid-exit re-simulation on the already-graded RS-leader entries, beside
the 21-EMA trail, the 5m-low ratchet, and the pass-1 Kalman nominee. MAD
robustness is outlier hygiene as an indicator — kin to the capped-R rule.

### P2-22. Volatility Expansion Score (0–4) — TotoMazter
`/script/r4ChVA9t-Volatility-Expansion-Score-0-4-v2-2-TotoMazter/` · open-source
**Mechanism:** one point per condition on each closed bar — ATR% in the
lower third of its 500-bar distribution (compressed), ATR% rising vs prior
bar, Bollinger width in the lower third of 120 bars, volume z-score > 0;
3/4 = threshold, 4/4 = signal. Self-normalized via rolling percentiles;
partial bars excluded.
**Data:** OHLCV (volume for the fourth point).
**Verdict: GRADEABLE.** A compression-then-ignition checklist — the coil
logic our flag/range lifecycle encodes structurally, as a numeric state.
Candidate conditioning tag (does expansion-score-at-arming sort spec
outcomes?); the author's own calibration is XAUUSD-1h, so nothing
transfers without our own grade.

**Covered in pass 1:** Zeiierman Bands (#15), Volatility Regime Tracker
(#18), ATR Delta (#21). **Redundant:** Relative Volume (RVOL) Percentile —
the RVOL family (P2-5).

## Category 4: Trend-following exits / trailing stops

### P2-23. NRTR Adaptive Trailing Reverse — MarkitTick
`/script/txN4hMDM-NRTR-Adaptive-Trailing-Reverse-MarkitTick/` · open-source
**Mechanism:** Nick Rypock Trailing Reverse on an optionally smoothed
source — in an uptrend the level tracks the highest smoothed value minus a
fixed-% or ATR offset, ratcheting up only; a close below flips the state
(mirror for down). Flips project stop + three R-multiple targets and
position size from fixed risk; optional ADX gate; optional cooldown window
blocking same-direction re-entry after a stop-out. Confirmed-bar alerts
only.
**Data:** OHLCV.
**Verdict: GRADEABLE.** Two nominable legs: the NRTR line as one more
trail variant in the hybrid-exit re-sim, and — the newer question — the
post-stop cooldown, gradeable on the 1m record as "does blocking immediate
re-entry after a stop improve the RS-leader day?" (The desk's rule is one
trade; the cooldown question generalizes it.)

### P2-24. Trend Trail, Trailing Stop & Buy Sell Signals — LunqFX
`/script/VGDBnkwq-Trend-Trail-Trailing-Stop-Buy-Sell-Signals-LunqFX/` · open-source
**Mechanism:** an ATR ratchet trail that only EXISTS when the market earns
it: Kaufman Efficiency Ratio (net move ÷ total path) percentile-ranked
against the symbol's own recent history must reach the top third to
activate the trail; hysteresis plus a minimum dwell time guard the regime;
in range the trail is dormant and rebuilds fresh on the next regime. Buy/
sell on closes through the opposite band; an unfiltered twin runs beside it
to display the noise reduction.
**Data:** OHLCV.
**Verdict: GRADEABLE — the gate is the candidate.** The ER-percentile
"when to trail at all" gate is separable from the trail itself and
pre-registerable on the RS-leader record: does efficiency-gating the
trail-after-1R beat trailing unconditionally? (Adaptive Trend Direction
Indicator [shitmemecoins], same listing, is the same core — ATR trail +
ER/ADX regime with hysteresis, self-declared as fitted on BTC-6h — noted
as this entry's family rather than written up separately.)

### P2-25. Uptrick: Adaptive Trend Trail — Uptrick
`/script/f4N0F439-Uptrick-Adaptive-Trend-Trail/` · open-source
**Mechanism:** nine weighted components (baseline distance 0.22, triple-
Supertrend consensus 0.20, momentum 0.19, two slopes, efficiency, RSI,
candle pressure, structure break) summed, EMA-smoothed, passed through a
dynamic gate that widens with chop and volatility; flips additionally
require Supertrend voting (2-of-3, 3-of-3 in high chop), persistence,
cooldown, and a takeover rule, with a strong-move bypass.
**Data:** OHLCV.
**Verdict: GRADEABLE, doctrine-flagged, low priority.** Hand-tuned
two-decimal weights and stacked discretionary gates — a large overfit
surface in the refused-composite family. Its parts (Supertrend consensus,
efficiency, cooldown) are all represented by cleaner entries above.

### P2-26. Supertrend Twincore — MachineSuiteAI
`/script/f8kVx1LK-Supertrend-Twincore-MachineSuiteAI/` · open-source
**Mechanism:** fast (2×ATR10) and slow (4×ATR20) Supertrends; signals only
on alignment, classified as structural flips (slow core) vs pullback
rejoins (fast core returning to the standing trend); whipsaw suppression
when the fast core flips twice in 10 bars; ADX < 20 forces NEUTRAL; signals
graded A/B/C by volume + structural confirmation, with per-grade historical
win rates tracked on-chart.
**Data:** OHLCV (volume for grade A).
**Verdict: GRADEABLE.** The joint fast/slow gate is the desk's own joint-
gate lesson (ema_bounce weekly vs daily) applied to one indicator at two
speeds, and it keeps its own per-grade record — kindred bookkeeping. The
"pullback rejoin" event class is adjacent to the RS-leader 8/21 pullback;
a grade should include the leader-day join. Tuned on crypto, transfers
nothing without our own numbers.

### P2-27. Chandelier Exit Trend Navigator — MarkitTick
`/script/mjOmsSXk-Chandelier-Exit-Trend-Navigator-MarkitTick/` · open-source
**Mechanism:** classic chandelier — long stop trails below the highest
extreme minus an ATR multiple, short stop mirrors above the lowest, the
active side ratcheting only with the trend; flip on close through the
opposite boundary; optional Kalman/slope-adjusted pre-smoothing of the
source; optional HTF-EMA bias and ADX gates; R-multiple TP ladder anchored
to one ATR of risk.
**Data:** OHLCV.
**Verdict: GRADEABLE — exit-variant candidate.** The chandelier anchors to
the highest high since entry — a mechanically different trail than the
21-EMA (mean-following) and the 5m-low ratchet (structure-following). A
natural third variant for the hybrid-exit re-sim on the graded entries;
the TP-ladder half stays in the refused bracket family.

### P2-28. Swing Trade Defender — BlueBeck
`/script/AUDyUchG-Swing-Trade-Defender/` · open-source
**Mechanism:** monitors an open swing position with five independent
failure flags — close through the 20-EMA against the trade, prior swing
point penetrated, RSI crossing under 50 and falling, a single bar closing
on ≥1.5× average volume (distribution), and a 2.5×ATR chandelier trail hit
on a close — voted into OK (0) / CAUTION (1–2) / EXIT (3+ flags, or any
trail hit). Days-to-weeks holds intended.
**Data:** daily OHLCV.
**Verdict: GRADEABLE — priority for the swing book.** All five flags are
computable from stored daily bars on the book's own open positions. The
honest first grade is a TAG (harness doctrine: it would filter/warn on the
same trades, not create different ones): stamp flag counts on open swing
positions daily, measurement-only beside `bearish_conflicts`, and grade
whether flag escalation preceded the losers on resolved trades. Promotion
asymmetric, like every tag.

**Skipped from this listing:** Trend Trigger (EMA filter + MTF stochastic)
and Momentum Bands Breakout — commodity MA/oscillator entry mashups; the
frequency-night verdict (entries are commodities, selection is the edge)
already prices this family. Darvas Box Ladder written up under
support/resistance (P2-15).

## Pass 2 shortlist (gradeability × novelty, candidates only)

1. **HOD/LOD time map** (P2-19) — when the day's extremes print, 20 years
   of 15m index bars, conditioned on the day-bias states; bears directly on
   the bell-exit and trail conventions.
2. **Break-quality components frozen at breakout** (P2-4) — path
   efficiency / close location / relative volume graded HELD-or-FAILED at
   our own pattern episodes.
3. **Swept vs broken level taxonomy** (P2-12) + **reaction-weighted level
   scoring** (P2-16) — the wick rule as level states, and reaction-magnitude
   ranking, both gradeable against the existing levels machinery.
4. **Exit-trail variants for the hybrid-exit harness** (P2-21 MAD, P2-27
   chandelier, P2-23 NRTR) plus the **efficiency-ratio "when to trail"
   gate** (P2-24) — all re-simulations on already-graded RS-leader entries.
5. **Time-of-day-matched relative volume** (P2-5) — the right denominator
   for the defense detector's volume baseline; a graded detector change,
   never a swap by feel.
6. **Dwell-time (time-at-price) shelf generation** (P2-14) — a level
   definition computable without volume attribution, new to our engine.
7. **Five-flag swing exit vote** (P2-28) — as a measurement tag on the
   swing book's own open positions first.
8. **Diagonal trendline lifecycle** (P2-9) — a level family we don't grade,
   with break/flip states ready for outcome tables.

Not gradeable from the stored record: VIX term structure (P2-17) — a data-
acquisition decision if ever wanted, surfaced for Eric rather than decided.

Same closing line as pass 1, binding here too: nothing in this pass is
armed, tagged, alerted, or traded by appearing in this file. Every
shortlisted item needs its own pre-registered spec — event, condition,
outcomes, baselines, era split, replication bar — before anyone reads a
number.
