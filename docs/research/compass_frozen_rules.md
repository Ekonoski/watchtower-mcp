# Compass — the frozen rules for the Beat-SPY sealed window

Status: **FROZEN 2026-09-07** on Eric's "run it". Rules content as of
commit c039634 (the last edit before the freeze); the sealed run ships in
the commit that follows, with `SEALED_VARIANTS` in `analysis/beat_spy.py`
naming exactly two runs — `v7_skip21_bonds` (primary, shares) and `v8_lev3`
(declared options expression) — each executed once on 2024-01-02 →
2026-09-04 under the once-only guard. Nothing below changes after this
line. Every number needed to reproduce the system is here.

## The system in one paragraph

Hold ONE US equity-index ETF at a time, chosen monthly by 12-month
momentum that ignores the most recent month, only while that momentum is
positive; when no index qualifies, hold long Treasuries if they qualify,
otherwise cash. Beside it, a small washout sleeve buys deep 16-day
green-dot bottoms on liquid stocks in major drawdown with a bounded
three-tranche ladder and a fixed hold. That is all.

## Sleeve A — index momentum (80% of equity)

- Universe: SPY, QQQ, IWM, MDY, DIA, RSP. Defensive: TLT only.
- Signal: on the last trading day of each month, for every fund,
  `momentum = close[t−21] / close[t−252] − 1` (12 months, skipping the
  last 21 trading days). A fund with no 252-bar history has no signal.
- Absolute filter: a fund qualifies only if its momentum is > 0.
- Ranking: qualifying equity funds best-first; hold the top 1. A current
  holding is kept while it stays inside the top 2; otherwise it rotates to
  the best. If NO equity fund qualifies, hold TLT if TLT's own momentum is
  > 0, else cash.
- Execution: at that month-end close, 5 bps per side. No stop inside the
  month; the only exits are the monthly re-rank and the absolute filter.
- No SPY-vs-200-day regime, no per-fund moving-average filter, no vol
  targeting (all tested, none earned a place — see CLAUDE.md).

## Sleeve B — deep green-dot ladder (20% of equity, 10 slots)

- Universe: stocks whose trailing-90-day average dollar volume is
  ≥ $10M as of the run date (a "liquid today" universe — survivorship and
  liquidity lookahead, stated). Leveraged and inverse ETPs excluded by
  name. Any ticker whose stored series is broken (a close 3× / ⅓ its
  prior close, or > 10 calendar days between bars) inside the prior two
  years is refused.
- Signal: a 16-trading-day wavetrend cross-up (fixed-anchor blocks on
  SPY's calendar, `greendot_dots`) with cross depth ≤ −30, on a stock
  ≥ 50% below its 2-year high, price ≥ $2 at the dot.
- Entry: slot budget = 20% of equity ÷ 10; three equal tranches — the
  first at the dot bar's close, the second at a resting limit 15% below
  it, the third 25% below it, each filling only when a later bar's low
  prints through the limit (filled at the limit, or the close if the bar
  closed below it).
- Exit: all tranches sold at the close 126 trading days after the dot
  bar (a fixed hold; no stop, no target). If the stock's stored tape
  breaks first, sold at the last real print.
- One position per ticker; a dot on a ticker already held is ignored;
  no new slot when 10 are open.

## Options expression (secondary, declared)

Same rules; Sleeve A's equity position is expressed as ~0.80-delta calls
with ~9 months to expiry on **3.0×** the share notional (variant
`v8_lev3`), priced by Black–Scholes on 60-day realized vol × 1.15, 2% of
premium per side plus $0.65 per contract, rolled when < 60 days remain,
total premium ≤ 25% of equity — the cap binds, so the realized leverage
is well under 3× and the excess falls back to shares. Modeled prices,
stated. TLT and the dots stay shares. (1.5× graded 12.04% / −27.0%,
2.0× 13.52% / −29.8%, 3.0× 14.14% / −30.3%; all three beat in both
halves; the cap, not the multiplier, sets the drawdown.)

## Benchmark and grading

Per `beat_spy_challenge_rules.md`: SPY total return with dividends
reinvested; beat = higher compound return AND no deeper max drawdown.
The system's own ETF dividends are NOT counted (price return only), a
stated bias against the system of roughly a point a year.

## Build-window record (2006-01-03 → 2023-12-31), hygiene v2

| | CAGR | Max DD | 2006–2015 | 2016–2023 |
|---|---|---|---|---|
| SPY total return | 9.61% | −55.2% | 6.96% / −55.2% | 13.15% / −33.7% |
| v7_skip21_bonds (primary) | 11.96% | −30.3% | 7.99% / −23.4% | 17.26% / −30.3% |
| v7_skip21_bonds_calls_1p5 | 12.04% | −27.0% | 7.50% / −23.4% | 18.22% / −27.0% |
| **v8_lev3 (declared options expression)** | **14.14%** | **−30.3%** | 9.49% / −23.4% | 20.38% / −30.3% |
| v7_skip21_bonds_nodots (Sleeve A alone) | 11.19% | −28.6% | 9.76% / −27.9% | 13.17% / −28.6% |

Refused on the same window (v8, the "great returns" pass): single-name
12-1 momentum on a liquid-at-the-date universe (top 5/10/20, with and
without a 200-day regime) graded 6–10% CAGR at −58% to −80% drawdowns —
2008 −56%, five straight losing years 2014–2018, 2019 +104%: a lottery
profile, and the regime-filtered version still breached SPY's drawdown
in 2016–2023 (−57.8% vs −33.7%). Longer dot holds (252 days) traded a
half-point of return for three points of drawdown (11.46% / −26.9%) and
the heavy version failed the first half (6.19% vs 6.96%); the 126-day
hold stands as declared.

Neighbors (same chassis): skip 15 → 10.38%, skip 30 → 9.98%, skip 42 →
11.61%, skip 10 → 9.56%; lookback 189 → 8.92%, 315 → 9.41%. The skip is a
plateau; the 12-month lookback is the published prior (Antonacci's GEM)
and its neighbors trail SPY by 0.2–0.7 pts — stated.

Sleeve split, primary: trend 29 trades, 21 wins, +$463k; dots 273 trades,
176 wins (64%), +$199k, avg win $1,850 / avg loss −$1,305, 5 defect
exits. Sleeve A alone beats in both halves on drawdown and ties SPY in
2016–2023 on return; the dots supply the 2016–2023 outperformance and
carry the survivorship caveat.

## Caveats, all of them

- Sleeve B universe is stocks liquid TODAY; the corpses' dots are unseen.
- Fills at the signal bar's close (momentum) and at limits a later bar
  traded through (ladder). No slippage beyond 5 bps.
- Option prices are modeled, not historical chains.
- `daily_prices` carries ticker-reuse splices and multi-year holes; the
  guard exits at the last real print and refuses dots on broken tapes.
  Stored price LEVELS on some names are off by a split factor while the
  daily ratios are real (FCEL 2019); P&L is ratio-based and unaffected.
- The build window was inspected many times (55 variants over ~3 hours).
  The sealed window is inspected once.

Stated odds before the sealed run: the drawdown clause is very likely to
hold (a one-ETF momentum book with an absolute filter sat out most of
2022); the return clause is close to a coin flip over 32 months in which
SPY compounded hard.

## SEALED RESULT (run once, 2026-09-07 16:27 UTC, commit 153dff1)

| 2024-01-02 → 2026-09-04 | CAGR | Max DD | Final equity | Trades |
|---|---|---|---|---|
| SPY total return | 21.39% | −18.8% | $167,862 | — |
| v7_skip21_bonds (primary) | 24.43% | −22.1% | $179,313 | 63 (37 wins) |
| v8_lev3 (options expression) | 24.43% | −22.1% | $179,313 | 63 — identical: zero calls opened |

**Verdict: FAILED the window under its own rules.** Clause 1 (return)
passed by 3.0 points; clause 2 (drawdown no deeper than SPY's) failed by
3.3 points — both troughs on 2025-04-08, the tariff crash, where the
system held QQQ against SPY's −18.8% and the dot sleeve fell with it.
Tie-break (return per unit of drawdown) also goes to SPY: 1.11 vs 1.14.
The stated odds were exactly backwards: the return clause held, the
drawdown clause did not, because a 32-month window with no bear market
gives the absolute filter nothing to sit out and leaves only the
concentration cost.

Year by year: 2024 +24.6% vs SPY +25.6%; 2025 +18.5% vs +18.0%; 2026 YTD
+20.3% vs +13.3%. Holdings, the entire trend sleeve: QQQ 2024-01-02 →
2025-04-30 (+$14.4k), SPY 2025-04-30 → 2026-02-27 (+$22.0k), QQQ
2026-02-27 → end (+$22.5k). Dots: 60 trades, 34 wins (57%), +$20.4k,
avg win $981 / avg loss −$499, zero defect exits.

**The options expression was vacuous out of sample.** In the build window
the 3× overlay opened only FIVE call positions in 18 years (DIA 2006–07,
DIA 2016, QQQ 2017–18 — the last two alone +$161k), because the 25%
premium cap admits a 3× deep-ITM position only when realized vol is
unusually low; 2024–26 never qualified, so the run collapsed to shares.
The 14.14% build number was five trades wearing a system's clothes. Any
future options expression must state how many positions its number
rests on.

Build → sealed drop-off: return went UP (11.96% → 24.43%, as did SPY's,
9.61% → 21.39%); the drawdown edge went from 25 points better than SPY
to 3 points worse. The system's edge is a bear-market edge and the
window held none. That is the honest reading, reported in the same
detail as a pass, per §7.
