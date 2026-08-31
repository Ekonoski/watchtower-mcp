-- 034: The tape-entry study + Eric's trade journal (2026-09-02).
--
-- tape-entry (Eric: "find the most liquid names for options and test
-- them to tell me which entries are the best regarding our indicator
-- watchtower bot... I'm assuming a retest of some level is the best
-- probability... then tell me via the study where the best stop is").
-- Universe = his scanner 7 + the rest of the mag-7 (11 names, every
-- one either verified liquid in our own iv_history OI record or a
-- perennial top-10 options venue). Entries = the Bot/Scanner's own
-- definitions (5m 8/21 EMA retest, 1m gated retest, ORB/PDH
-- break-retest, break-chase as the control for the retest assumption).
-- Stops = a pre-registered grid graded on the same entries.
--
-- trade_journal: Eric's MANUAL trades — his book, never the paper
-- desk's. Written via MCP (Eric or the Grok bot may log entries; the
-- Grok read-only rule covers code and the DESK ledger, not this).

CREATE TABLE IF NOT EXISTS liquid_1m_bars (
    ticker      text NOT NULL,
    ts          timestamptz NOT NULL,
    trade_date  date NOT NULL,
    open        numeric NOT NULL,
    high        numeric NOT NULL,
    low         numeric NOT NULL,
    close       numeric NOT NULL,
    volume      numeric,
    PRIMARY KEY (ticker, ts)
);
CREATE INDEX IF NOT EXISTS idx_liquid_1m_tk_date ON liquid_1m_bars (ticker, trade_date);

-- One row per graded ticker-day, entries or not: zero events is a
-- recorded quiet read; a missing row is ungraded (the _social_block rule).
CREATE TABLE IF NOT EXISTS tapeentry_days (
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    n_events    integer NOT NULL,
    PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE IF NOT EXISTS tapeentry_events (
    id            bigserial PRIMARY KEY,
    trade_date    date NOT NULL,
    ticker        text NOT NULL,
    family        text NOT NULL,      -- ema8_5m / ema21_5m / ema_1m_gated / orb_rt / pdh_rt / orb_chase / pdh_chase
    direction     text NOT NULL,      -- long / short
    entry_ts      timestamptz NOT NULL,
    entry_px      numeric NOT NULL,
    struct_px     numeric,            -- pullback-extreme stop level (NULL where no pullback bar: chase entries)
    atr_pct       numeric,            -- ATR14(5m) at entry as % of entry
    eod_bps       numeric NOT NULL,   -- signed, entry -> true close, no stop
    mfe_bps       numeric NOT NULL,
    mae_bps       numeric NOT NULL,
    stops         jsonb NOT NULL,     -- {variant: {out, exit_px, bps, r}}
    UNIQUE (trade_date, ticker, family, direction)
);
CREATE INDEX IF NOT EXISTS idx_tapeentry_fam ON tapeentry_events (family, direction);

CREATE TABLE IF NOT EXISTS trade_journal (
    id            bigserial PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    source        text NOT NULL DEFAULT 'eric',   -- who logged it: eric / grok / claude
    ticker        text NOT NULL,
    direction     text NOT NULL,                  -- long / short
    instrument    text,                           -- shares / call / put / spread
    setup         text,                           -- the Scanner/Bot vocabulary: "8 BULL retest", "ORB break", ...
    timeframe     text,                           -- the chart the decision was made on
    entered_at    timestamptz,
    exited_at     timestamptz,
    entry_px      numeric,                        -- underlying price at decision (options: still log the underlying)
    exit_px       numeric,
    stop_px       numeric,
    target_px     numeric,
    qty           numeric,
    pnl_dollars   numeric,
    r_multiple    numeric,                        -- computed from entry/stop/exit in UNDERLYING terms when derivable
    note          text,                           -- what the eye saw, verbatim
    mistakes      text                            -- the honest column: what broke the plan, if anything
);
CREATE INDEX IF NOT EXISTS idx_journal_date ON trade_journal (entered_at);
