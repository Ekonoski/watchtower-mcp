-- 020: The intraday structure watcher's record (2026-08-24).
--
-- The nightly structure screen finds the broken major levels; the
-- 15-minute watcher follows them through the session and records
-- every touch's defense verdict (find_defense v1 at the shelf).
-- Defended retests ping Discord once per (day, ticker, level); every
-- verdict persists here so forward-return grading reads a record.

CREATE TABLE IF NOT EXISTS structure_watch (
    trade_date  date        NOT NULL,
    ticker      text        NOT NULL,
    level       numeric     NOT NULL,
    touches     int,
    touch_at    timestamptz,
    status      text        NOT NULL,  -- defended|no_defense|missed|knife_skipped|unavailable
    defense_px  numeric,
    defense_at  timestamptz,
    premium_pct numeric,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, ticker, level)
);

ALTER TABLE structure_watch ENABLE ROW LEVEL SECURITY;
