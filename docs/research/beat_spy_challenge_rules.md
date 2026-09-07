# The Beat-SPY Challenge — rules for every contestant

Frozen 2026-09-07. Two contestants (Watchtower/Claude and ChatGPT) build a
trading system independently. Both are graded on the same sealed window
under the same definition of "beat". These rules are written down before
either contestant sees the sealed data, so the result is a race and not
two stories.

## 1. Benchmark

- SPY total return, dividends reinvested, from the first trading day of
  the sealed window to its last, with the same starting capital as the
  system.
- Starting capital: $100,000 (a scale, not a promise; sizing rules must
  work at this size without moving prices).

## 2. What "beat" means

A system beats SPY only if BOTH hold over the sealed window:

1. Higher compound return than SPY total return.
2. Maximum drawdown no deeper than SPY's maximum drawdown over the same
   window (peak-to-trough on end-of-day equity).

A higher return with a deeper drawdown is leverage, not edge, and does
not count. Report both numbers, plus: number of trades, win rate, average
win and average loss in dollars, longest flat stretch, and time in the
market (% of days with any position).

## 3. Instruments

- US-listed stocks and ETFs, long or short (shorts allowed only in shares
  or via long puts; no naked short options).
- Options allowed, **defined risk only**: long calls, long puts, debit
  spreads. No naked short options, no margin, no leveraged/inverse ETFs
  beyond 1x.
- Total option premium at risk may never exceed 25% of equity; any single
  position may never risk more than 2% of equity to its stated stop or,
  for options, its full premium.

## 4. Fills, costs, data

- Fills only at prices that printed: the close of the signal bar or a
  later bar, never the signal bar's open/high/low, never a limit price
  the bar did not trade through.
- Shares: 5 basis points per side. Options: modeled or historical prices
  are allowed, but the contestant must state which; a flat spread charge
  of 2% of premium per side plus $0.65 per contract applies to modeled
  prices.
- No lookahead of any kind. Every signal must be computable from data
  available at the bar it fires on. Indicators that repaint are
  disqualified.
- Survivorship: state the universe and when its membership was fixed.
  A universe of "stocks listed today" is allowed but must be labeled.

## 5. Build window and sealed window

- **Build window: all data through 2023-12-31.** Rules may be designed,
  tuned and tested freely here.
- **Sealed window: 2024-01-02 through 2026-09-04.** The rules are FROZEN
  in writing (a dated document or commit hash) before the sealed window
  is run. It is run ONCE. No parameter changes, no re-runs, no "version
  2" on the same window. A contestant who changes anything after seeing
  sealed results forfeits the window.
- The rules document must contain every number needed to reproduce the
  system: universe, signals, entry, exit, stop, sizing, regime switch,
  rebalancing schedule, and what the system holds when it has no signal
  (cash or SPY).

## 6. Reporting

Each contestant delivers, for the sealed window:

- equity curve (end-of-day), compound return, max drawdown, vs SPY;
- the trade list with entry/exit dates and prices;
- the frozen rules document and its date/hash;
- the build-window result for the same rules, so the drop-off from build
  to sealed is visible;
- every caveat: modeled option prices, survivorship, cost assumptions.

## 7. Tie-breaks and honesty

- If both beat SPY, higher return per unit of max drawdown wins.
- If neither beats SPY, the one with the smaller drawdown loses less and
  that is the ranking; nobody claims a win.
- A failed holdout is reported in the same detail as a pass.
