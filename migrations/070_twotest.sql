-- 070: the two-test entry engine study (2026-09-12 — Eric's intraday
-- level-trading workbook: completed 5m close through a level, then on
-- the 1m a retest low, a bounce high, a higher low, entry on the cross
-- of the bounce high). One row per (day, name, level family): the
-- non-entries are recorded (no_level / no_confirm / no_trigger /
-- structure_failed) because a no-trigger failure is a correct pass and
-- the workbook wants it counted. Applied live via MCP.

CREATE TABLE IF NOT EXISTS twotest_events (
    id           bigserial PRIMARY KEY,
    trade_date   date NOT NULL,
    ticker       text NOT NULL,
    family       text NOT NULL,
    direction    text NOT NULL,
    level_px     numeric,
    status       text NOT NULL,
    why          text,
    confirm_ts   timestamptz,
    l1_px        numeric,
    h_px         numeric,
    l2_px        numeric,
    trigger_ts   timestamptz,
    entry_px     numeric,
    stop_px      numeric,
    obstacle_px  numeric,
    room_r       numeric,
    eod_bps      numeric,
    mfe_bps      numeric,
    mae_bps      numeric,
    stops        jsonb,
    tp1          jsonb,
    bracket2r    jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trade_date, ticker, family)
);
CREATE INDEX IF NOT EXISTS twotest_events_status ON twotest_events (status, trade_date);

CREATE TABLE IF NOT EXISTS twotest_days (
    ticker       text NOT NULL,
    trade_date   date NOT NULL,
    n_bars       integer,
    n_events     smallint,
    n_triggered  smallint,
    PRIMARY KEY (ticker, trade_date)
);
