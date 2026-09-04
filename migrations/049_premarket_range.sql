-- 049: premarket range (2026-09-03) — a research backfill of the 04:00-
-- 09:29 ET high/low per liquid name per day, so the premarket high can
-- be a graded take-profit level. pm_bars=0 is a quiet read; a missing
-- row is a hole. Applied live via MCP.

CREATE TABLE IF NOT EXISTS premarket_range (
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    pm_high     numeric,
    pm_low      numeric,
    pm_bars     integer NOT NULL DEFAULT 0,
    PRIMARY KEY (ticker, trade_date)
);
