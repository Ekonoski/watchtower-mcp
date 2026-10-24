# Rotation Book — pre-registered spec (frozen 2026-10-24, before any number)

Purpose: Eric's manual book. Hold 1–3 weeks (extension to 1–3 months only when
the exit rule keeps the trade), traded on ~10x leverage, so the path (adverse
excursion, overnight gaps) is graded beside the return. R = $250 per trade,
sized as risk / per-share stop distance, rounded down.

## Universe (as of each signal date, no lookahead)
- `tickers` with a GICS sector; funds excluded (industry Asset Management /
  Shell Companies) and leveraged/inverse ETPs excluded by the house
  `LEVERAGED_ETP_RE` on company name.
- Close ≥ $5; 20-day average dollar volume ≥ $10M.
- ≥ 60 bars of history and a full 50-bar window; the 252-bar high needs ≥ 126
  bars (else the "not at highs" leg is a hole and the row is skipped).

## Sector rotation state (breadth definition — the house `sector_rs.py` formula)
Per trading day, over every universe ticker with ≥ $2M/day (a light floor so
penny names don't set the median — stated deviation from the cache, which has
no floor and includes funds):
- r1w / r1m / r3m = 5 / 21 / 63-bar close-to-close returns.
- Sector median minus the all-stock median → rs_1w, rs_1m, rs_3m.
- rank_1m, rank_3m: 1 = strongest of 11 sectors that day.
Reconciliation test: on dates where `sector_rs_daily` exists (2024-08 →), my
rank_1m must agree with the cache on ≥ 80% of sector-days; if not, the
definition is wrong and the study stops.

States, each tested as its own variant:
- **ST1 week-turn** (the one cell the 2026-08-22 sector study found positive):
  rank_1m ≥ 8 AND rs_1w > 0 — sector washed out on the month, turning up on
  the week.
- **ST2 dashboard "rotating in"** (the exact rule in
  watchtower/analysis/sector_rotation.py, read 2026-10-24 before any sector
  cut was run): rank_3m − rank_1w ≥ 2 AND rank_1w ≤ 6 on median returns —
  the sector climbed at least two spots from its quarter rank to its week
  rank and sits in the better half this week. (Amended from my first
  encoding, "rank_3m ≥ 8 AND rank_1m ≤ 4", once the dashboard code was
  read; the original is kept as **ST2b** for the record.)
- **ST3 any sector** — the control for the sector leg.
- **ST4 leader chase** (expected to lose, included so the comparison is
  honest): rank_1m ≤ 3 AND rank_3m ≤ 3.

## Stock setup — "reclaim-retest, confirmed" (one definition, daily bars)
All conditions at the signal close, bar t:
1. `close > SMA50` (the reclaim is live).
2. Fresh: of bars t-30..t-11, ≥ 12 closed below the SMA50; of bars t-10..t-1,
   ≥ 1 closed below it (the cross happened inside the last 10 sessions).
3. Pulled back into the zone: min(low, t-4..t) ≤ max(SMA50, EMA21) × 1.01.
4. Holding: close ≥ EMA21.
5. **Confirmed**: close > previous bar's high (a close back up through
   yesterday's high — the journal's `confirmed` leg, never `anticipated`).
6. Not at highs: close < 0.97 × 252-bar high (the inverted leg from the
   2026-09-12 sector-leader study).
7. Leverage cap: stop distance ≤ 6% (below).
8. No earnings report (earnings_history) in (t, t + 21 calendar days].
Ranking features recorded, never gating: vol20/vol60 (expanding volume), the
stock's 21-bar return vs its sector median (laggard/leader), distance from the
252-bar high, stop distance.
Cooldown: a signal within 21 bars of a prior signal on the same ticker is
dropped (one trade per name per month).

## Entry
Next session's OPEN (t+1). 5 bps each way on entry and exit.

## Stop and exits (graded on the same entries)
- Stop level: min(low, t-9..t) × 0.9975 (under the pullback low); exit on a
  daily CLOSE below it (wick rule), at that close. Gap-through = that close.
- **E1** fixed hold: exit at the close of bar t+10.
- **E2** stop + cap: close-rule stop, else exit at the close of bar t+21.
- **E3** stop + trail: close-rule stop; from bar t+6 on, exit on the first
  close below the 8 EMA; else t+21 cap. (The "longer hold if the opportunity
  is there" clause — extension beyond 21 bars is NOT tested here; it is a
  separate study if E3 earns it.)
R = (exit − entry) / (entry − stop), capped ±10 at readout, NULL guarded.

## Path metrics (the leverage read)
Per trade over bars t+1..t+21: MAE (min low vs entry), MFE (max high vs
entry), worst overnight gap (open vs prior close) after entry; share of trades
with MAE ≤ −5%, ≤ −8%, ≤ −10% (≤ −10% ≈ liquidation at 10x before any stop);
share stopped on a gap (open already below the stop).

## Baselines
- **C1 sector-day pool**: every universe name in the same sector on the same
  entry date passing only the liquidity filter — mean forward return, mean
  MAE, share ≤ −8/−10%. Each trade is compared to its own pool.
- **C2** the setup under ST3 (no sector condition) — isolates the sector leg.
- **C3** SPY over the same windows.
- Forward returns at closes t+5, t+10, t+21, t+42, t+63 from the entry open.

## Bar (frozen)
The rule set (setup × a sector state) PASSES only if, in BOTH eras
(2005–2015 and 2016–2026) and with ≥ 100 trades per era:
(a) mean 10-day and 21-day return beats the C1 pool;
(b) the sector state beats ST3 (the sector leg adds, not just the setup);
(c) under E2 or E3, mean R > 0 and the share of trades with MAE ≤ −10% is
    below the pool's.
Anything that fails (a)–(c) is reported as failed, by leg. No re-cutting after
the readout; a new cut is a new study with its own name.

## Stated caveats
- Survivorship: pre-2021 delistings never entered daily_prices; the universe
  is ~1,000–1,750 names before 2022 and 8,000+ after (vendor expansion) —
  early-era results are survivor-heavy and large-cap-heavy; the pool control
  shares the bias.
- Opens as fills, no slippage beyond 5 bps, no borrow/financing cost for the
  leverage, daily bars only (intraday liquidation at 10x is a touch, graded
  here by the daily low).
- Earnings dates from earnings_history (vendor); misses are holes, not
  "no earnings".
- The dashboard's `sector_rotation` table only exists from 2026-06; ST2 is my
  encoding of that family, not the dashboard's code.

## Tables (research, prefix rotbook_)
rotbook_sector_daily, rotbook_trades, rotbook_pool, rotbook_progress.
