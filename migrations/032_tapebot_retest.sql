-- 032: The Tape Bot retest-machine study (2026-08-29, Eric: "this
-- indicator could be a game changer... should we add a version of it
-- to our autonomous trading?"). The Pine state machine's fresh
-- RETEST/HELD signals graded at PDH/PDL on the stored SPY/QQQ 15m
-- record. Phase 1 levels are PDH/PDL only — ONH/ONL is a DECLARED
-- hole (the stored bars are RTH-only; overnight needs a backfill).
-- Outcomes are in the TRADE's direction (positive = the signal paid).

CREATE TABLE IF NOT EXISTS tapebot_retest_events (
    id            bigserial PRIMARY KEY,
    ticker        text NOT NULL,
    trade_date    date NOT NULL,
    level_name    text NOT NULL,           -- PDH / PDL
    level_px      numeric NOT NULL,
    event         text NOT NULL,           -- retest_bull/retest_bear/held_bull/held_bear
    direction     text NOT NULL,           -- long / short
    signal_ts     timestamptz NOT NULL,
    entry_px      numeric NOT NULL,        -- the signal bar's close
    day_close_px  numeric,
    fwd_close_bps numeric,                 -- entry -> true close, trade direction
    mfe_bps       numeric,                 -- best excursion to close, trade direction
    mae_bps       numeric,                 -- worst excursion to close, trade direction
    bars_to_close integer,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticker, signal_ts, level_name, event)
);

CREATE INDEX IF NOT EXISTS idx_tapebot_retest_tk_date
    ON tapebot_retest_events (ticker, trade_date);
