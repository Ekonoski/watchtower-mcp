# Rotation Book — readout (2026-10-24, run once on the frozen spec)

Spec: `rotation_book_spec.md` (frozen before any number). Research tables in
the Watchtower DB: `rotbook_sector_daily`, `rotbook_trades` (49,319 raw
signals 2005→2026-10-23), `rotbook_pool`, `rotbook_events` (per-trade
outcomes). Reads below exclude earnings-inside-hold and cooldown hits, and
subtract 10 bps round-trip. R capped ±10, NULL-guarded.

## Universe defect found at readout (stated, corrected at readout only)
The fund exclusion matched the industry string "Asset Management" exactly;
the tickers table also files ~1,700 funds under "Asset Management - Global /
Bonds / Income / Leveraged / Cryptocurrency". 3,387 of 5,233 Financial
Services trades were funds (muni bond ETFs dominated the Monday list). All
numbers below exclude industry ILIKE 'asset management%' and fund-named
tickers. The sector medians were NOT re-run: a uniform shift of the market
median leaves the 11-sector ranks unchanged, so the other ten sectors' states
stand; Financial Services' own state is unreliable and its trades are a small
share after the exclusion.

## Headline — the setup (all sectors), net of costs
| | post-2016 | pre-2016 |
|---|---|---|
| trades | 9,164 | 3,440 |
| 10-day: setup / sector pool / SPY | +12 / +18 / +53 bps | +18 / +10 / +21 bps |
| 21-day: setup / pool | +31 / +45 | +38 / +34 |
| 63-day: setup / pool | +111 / +152 | +156 / +136 |
| 10-day win rate | 50.9% | 53.3% |
| E1 fixed 10-day, R | +0.08 | +0.02 |
| E2 stop + 21-day cap, R (win) | +0.012 (46%) | +0.067 (49%) |
| E3 stop + 8-EMA trail, R (win) | −0.016 (40%) | +0.012 (42%) |
| avg stop distance | 4.5% | 4.4% |
| stopped (close rule) | 38% | 36% |
| gap-stopped (open already below stop) | 9.3% | 9.4% |
| MAE ≤ −10% within 21 bars (no stop) / pool | 18.2% / 24.5% | 14.7% / 18.8% |
| low ≤ −10% before the E3 exit (liquidation at 10x) | 1.3% | 0.9% |
| overnight gap ≤ −10% inside the hold | 1.1% | 0.7% |

## Sector states (same setup, net), 10-day / 21-day / 63-day vs pool; E3 R
| state | era | n | 10d | 21d | 63d | E3 R |
|---|---|---|---|---|---|---|
| ST1 week-turn | post | 883 | +4 vs +5 | −20 vs +18 | +69 vs +113 | −0.047 |
| | pre | 382 | −16 vs −27 | −39 vs −12 | +37 vs +72 | −0.032 |
| ST2 dashboard "rotating in" | post | 3,126 | −6 vs +5 | +21 vs +45 | +87 vs +137 | −0.041 |
| | pre | 1,122 | −1 vs −9 | +9 vs +12 | +140 vs +129 | −0.012 |
| ST2b qtr-washout / month-inflow | post | 840 | +24 vs +9 | +25 vs +38 | +20 vs +52 | +0.042 |
| | pre | 331 | +44 vs +5 | +104 vs +40 | +218 vs +167 | +0.075 |
| ST4 leader chase | post | 1,110 | +19 vs +54 | +44 vs +82 | +110 vs +151 | −0.053 |
| | pre | 464 | +59 vs +47 | +78 vs +106 | +153 vs +199 | +0.049 |

ST2b by year (the one cell that beat its pool at 10 days in both eras):
E3 R negative in 2008, 2011, 2015, 2016, 2017, 2018, 2019, 2022; n per year
9–207. Post-2016 it trails its pool at 21 and 63 days.

## Gradients (post / pre, 10-day bps vs pool)
- Volume expansion ≥ 1.5× (n=244/49): +39 vs +2 / −47 vs −10 — sign flips.
- Distance from 252-day high: deeper = higher return AND higher −10% MAE share
  (25–50% off: 26%; >50% off: 39% vs 9% near highs) — return and blow-up
  risk move together; untradeable at 10x.
- Stop < 2% of price: −10 bps, −0.18R post — too tight to survive noise.
- Laggard vs its sector: +73 vs +75 post / −44 vs −34 pre — sign flips.
- Sector up on the week: +17 vs +12 / +18 vs +10 — ~5–8 bps, both eras.
- Earnings inside the hold: same return, −10% MAE share 24% vs 15%, gap ≤ −10%
  3.3% vs 0.9% — the exclusion is worth keeping.

## Verdict against the frozen bar
(a) beat the sector pool at 10 and 21 days in both eras — **FAIL** (post-2016
below the pool at every horizon; below SPY in both eras).
(b) the sector leg adds — **FAIL** for ST1, ST2 (the dashboard's rule is the
worst cell post-2016) and ST4; ST2b passes at 10 days in both eras, fails at
21 days post-2016, and is sign-unstable by year.
(c) E2/E3 mean R > 0 in both eras — **FAIL** (E3 negative post-2016; E2 ≈ 0
after costs); the −10% MAE share IS below the pool's (the only clause met).

The setup is market beta with a slightly tighter drawdown profile than
random names in the same sectors. Sector rotation, as defined three ways,
does not rescue it. Not an edge. Not tradeable on 10x: a stopped trade costs
~45% of posted margin, ~9% of trades gap through the stop, ~1% of trades see
an overnight gap that wipes the margin.

## Monday 2026-10-27 list under the frozen rules
After removing funds and names with earnings inside the 21-day hold: none.
Candidates refused by the earnings leg: SIRI (10/29), STRA (11/5), FANG
(11/2), ESQ (10/27), CWT (10/28), ARCO (11/12), CRC (11/2), PECO (10/26),
FTV (10/28), INGM (10/29). WDS (Woodside) has no earnings date in the
record (a hole, not a pass) and sits in Energy, a leader-state sector (the
worst state vs its pool). Zero picks is the rule set's answer in earnings
season, and it is data.

Caveats: opens as fills, 5 bps each way, no financing cost, daily bars
(intraday liquidation graded by the daily low), survivors-heavy before 2022,
8,000-name universe after. No re-cutting after this readout; a new idea is a
new spec.
