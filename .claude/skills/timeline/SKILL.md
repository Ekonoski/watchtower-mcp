---
name: timeline
description: "Render a Watchtower ticker timeline card: six months of daily candles with the system's own calls pinned to the bars where they happened, plus fundamentals, levels-and-why, a desk verdict, and the mandatory skeptical note. Invoke with a ticker (/timeline ACHR). Use when the user asks for a timeline card, a chart post-mortem, or a 'what happened to X and what did we say' review. A plain quote or fundamentals question is not a timeline request; answer it directly instead."
---

## What this card is

The graded ledger made visual: price action with its causes pinned to it, where the
pins include Watchtower's *own receipts* (scanner AVOIDs, gap alerts, oscillator
calls) graded honestly against what happened next. It is not a news chart —
adjacency is never causality, and the card says so out loud.

The worked reference is `assets/reference-achr.html` (published 2026-08-07, ACHR
into its Q2 print). Start from it: same tokens, same anatomy, same tone. Change
what the data demands, not the structure.

## Procedure

### 0. Binary check first

Before anything else, find the next earnings date and **verify it against the
company's own release or two independent sources** — the house calendar has had
wrong/unconfirmed dates (GILD, BKNG, CELH, USNA all struck in one week). If a
print lands within ~5 sessions, the card's verdict section is written around it
and the desk's skipped-binary doctrine applies. If the date can't be verified,
say so on the card.

### 1. Gather (Supabase MCP, one round trip each)

Daily bars, ~6 months (also serves the volume pane):

```sql
SELECT json_agg(json_build_array(trade_date, open, high, low, close, volume) ORDER BY trade_date)
FROM daily_prices WHERE ticker='<T>' AND trade_date >= (CURRENT_DATE - interval '6 months');
```

Everything else in one payload (all tables verified to exist; column names are
exact — `trade_date`, `as_of_date`, `event_date`, `report_date`, `snapshot_date`
differ per table and have bitten before):

```sql
SELECT json_build_object(
 'valuation', (SELECT row_to_json(v) FROM (SELECT as_of_date, rev_ttm, ni_ttm, ebitda_ttm, fcf_ttm,
     total_debt, cash, shares_outstanding, price, market_cap, ps, pb
     FROM valuation_metrics WHERE ticker='<T>' ORDER BY as_of_date DESC LIMIT 1) v),
 'scores', (SELECT row_to_json(f) FROM (SELECT as_of_date, altman_z_score, piotroski_score
     FROM financial_scores WHERE ticker='<T>' ORDER BY as_of_date DESC LIMIT 1) f),
 'estimates', (SELECT json_agg(row_to_json(e)) FROM (SELECT fiscal_year, eps_avg, revenue_avg, num_analysts
     FROM analyst_estimates WHERE ticker='<T>' ORDER BY fiscal_year LIMIT 4) e),
 'oscillator', (SELECT json_agg(row_to_json(o)) FROM (SELECT timeframe, bar_ts, rsi, macd_hist, signals,
     confluence_score, direction FROM oscillator_scan WHERE ticker='<T>' ORDER BY scanned_at DESC LIMIT 4) o),
 'patterns', (SELECT json_agg(row_to_json(p)) FROM (SELECT timeframe, pattern, direction, status, trigger_price,
     target, invalid_level, last_close, score, scanned_at FROM pattern_scan WHERE ticker='<T>'
     ORDER BY scanned_at DESC LIMIT 6) p),
 'earnings_hist', (SELECT json_agg(row_to_json(h)) FROM (SELECT report_date, eps_actual, eps_estimated,
     revenue_actual, surprise_pct FROM earnings_history WHERE ticker='<T>'
     AND report_date >= (CURRENT_DATE - interval '7 months') ORDER BY report_date) h),
 'earnings_next', (SELECT row_to_json(c) FROM (SELECT report_date, eps_estimated, revenue_estimated,
     time_of_day, confirmed, last_4q_surprise_avg FROM earnings_calendar WHERE ticker='<T>'
     AND report_date >= CURRENT_DATE ORDER BY report_date LIMIT 1) c),
 'alerts', (SELECT json_agg(row_to_json(a)) FROM (SELECT alert_date, alert_type, signal_type, entry_price, score
     FROM alert_log WHERE ticker='<T>' AND alert_date >= (CURRENT_DATE - interval '6 months')
     ORDER BY alert_date) a),
 'grades', (SELECT json_agg(row_to_json(g)) FROM (SELECT event_date, grading_company, previous_grade, new_grade,
     action FROM analyst_grades WHERE ticker='<T>' AND event_date >= (CURRENT_DATE - interval '6 months')
     ORDER BY event_date) g),
 'shorts', (SELECT row_to_json(s) FROM (SELECT as_of_date, short_percent_of_float, short_ratio
     FROM short_interest WHERE ticker='<T>' ORDER BY as_of_date DESC LIMIT 1) s),
 'vol', (SELECT row_to_json(vv) FROM (SELECT as_of_date, realized_vol_20d, atr_pct_price, beta_vs_spy,
     return_1m_pct, return_3m_pct, vol_regime FROM volatility_metrics WHERE ticker='<T>'
     ORDER BY as_of_date DESC LIMIT 1) vv),
 'buzz', (SELECT json_agg(row_to_json(b)) FROM (SELECT snapshot_date, rank, mentions, sentiment FROM social_buzz
     WHERE ticker='<T>' AND snapshot_date >= (CURRENT_DATE - interval '6 months') ORDER BY snapshot_date) b),
 'headlines', (SELECT top_headlines FROM news_sentiment WHERE ticker='<T>' ORDER BY as_of_date DESC LIMIT 1)
);
```

Then one web pass for anything the print/binary needs (verify the earnings date,
catch a catalyst our feeds missed). Today's bar may be intraday-partial —
compare against a live quote and stamp the footer accordingly.

### 2. Choose pins (max ~10, chronological, numbered)

Priority order — drop from the bottom, never silently truncate (say what was cut
if the tape is busier than 10):

1. **Earnings prints** in-window: actual vs estimate, surprise, and what price did *after* (a beat that faded is information).
2. **System receipts** from `alert_log`: AVOID / WATCH ≥ 70 score, GAP_AND_GO, oscillator composite alerts. **Grade each honestly against subsequent bars** — including adverse excursion on calls that eventually paid ("early is a cost").
3. **Structure bars**: the swing low/high that current patterns are anchored to, higher-low / lower-high bars that create or kill a pattern.
4. **Volume events**: days ≥ 3× average volume.
5. **Analyst actions** (upgrades/downgrades/initiations — maintains rarely earn a pin) and **load-bearing headlines** from `top_headlines`.
6. Big unexplained moves get pinned as exactly that: "no single catalyst in our feeds" — never backfill a story.

Future pin `E`: the next earnings date, in a shaded zone right of the last bar,
with estimate and any company-guided number.

### 3. Levels (right rail, each with a why)

From `pattern_scan`: triggers, targets, invalidation levels. Call out
collisions — the best cards are built around one price where structures collide
(ACHR: 5.60 was two weekly invalidations + a daily trigger + Friday's exact
high). Off-chart targets get a table row, not a fake axis.

### 4. Build the card

Start from `assets/reference-achr.html`. Anatomy, in order: kicker · headline
(the card's single most load-bearing fact, not a summary) · posture chips ·
chart panel · two columns: "The tape, graded" (numbered, matching pins) beside
fundamentals strip / levels-and-why / **desk verdict** / **skeptical note** ·
freshness footer.

Chart rules (validated 2026-08-07, `dataviz` skill procedure):

- House tokens, dark-first with the light `@media` block and both `data-theme`
  overrides — copy the reference's token block verbatim.
- Candles: **hollow body = up close, filled = down close** (shape carries
  direction so color never stands alone). Up `--pin` teal, down `--slip` ember —
  this pair passes CVD checks in both themes; do not add a third series color.
  Gold `--flip` is reserved for levels, pins, and the decision line.
- Volume pane below; bars ≥ 3× average rendered at full opacity, rest faded.
- Hover tooltip per candle (date + OHLC + volume).
- Numbered pin circles with leader lines, alternating above/below; label ≤ 14 chars.
- Sparse y gridlines, month ticks, ~168px right margin for level labels.

Before publishing: screenshot **both themes** with headless Chromium
(`data-theme` stamp for the second) and actually look — label collisions,
pin overlaps, level-label crowding are the recurring bugs.

### 5. House doctrine (non-negotiable, from CLAUDE.md)

- Freshness stamped **per source** in the footer: bars, scans (and what close
  they ran on), fundamentals, short interest, buzz, grades. A scan run pre-open
  did not see today's candle — say so.
- A thin feed is a **coverage gap, not consensus** (two analyst maintains ≠
  "analysts agree"). An errored lookup renders as *unavailable*, never neutral.
- **Skeptical note is mandatory** and must contain at least one fact that cuts
  against the card's own headline read.
- Numbers on one line reconcile: derive returns from the bars on the card and
  name reference closes.
- The desk verdict follows the playbook: binary within ~5 sessions →
  skipped-binary framing, post-print trigger named; otherwise trigger / stop /
  target with the completed-close (no-wick) rule stated.

### 6. Publish and grade

- File: `timeline-<ticker>.html` in the session scratchpad. **One artifact URL
  per ticker** — re-running the same ticker edits that file and republishes to
  the same URL (pass the prior URL via `url` from a new conversation; find it
  with the Artifact tool's list action). Favicon 📍, label like
  `v2-post-print`.
- **Self-grading:** on any re-run after a pinned event resolves (especially pin
  E), the old card's verdict becomes a pin on the new card, graded. The card is
  accountable to its own calls — that is the product.
