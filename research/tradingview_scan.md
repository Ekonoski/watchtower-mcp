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
