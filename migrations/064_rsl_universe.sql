-- 064: the RS-leader UNIVERSE study (2026-09-08, Eric: "tell me which
-- names are the best for what I do and then run it through to see if
-- they are all good fits for the morning leader board"). Same columns
-- as rs_leader_events plus the universe the rank was computed in:
-- 'mag7' (control — must reproduce rs_leader_events), 'mag7+AVGO' etc.
-- (each candidate alone against the incumbents), 'mag12' (all).
-- Applied live via MCP.

CREATE TABLE IF NOT EXISTS rsl_universe_events (
    id          bigserial PRIMARY KEY,
    universe    text NOT NULL,
    trade_date  date NOT NULL,
    ticker      text NOT NULL,
    role        text NOT NULL,
    direction   text NOT NULL,
    rs_945_pct  numeric,
    entry_kind  text NOT NULL,
    entry_ts    timestamptz,
    entry_px    numeric,
    stop_px     numeric,
    outcome     text,
    r_first     numeric,
    r_noon      numeric,
    r_close     numeric,
    mfe_r       numeric,
    mae_r       numeric,
    UNIQUE (universe, trade_date, ticker, role, entry_kind)
);
CREATE INDEX IF NOT EXISTS idx_rslu_universe_role ON rsl_universe_events (universe, role, trade_date);
