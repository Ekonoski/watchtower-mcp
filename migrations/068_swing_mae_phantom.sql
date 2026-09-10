-- 068: the MAE read's free audit found stops printed on a bar that was
-- not the daily close (2026-09-10). Each row now carries the exit day's
-- OFFICIAL close in R and a phantom flag (exit_reason = 'stop' while
-- that close held at/above the stop). Applied live via MCP.

ALTER TABLE swing_mae_events
    ADD COLUMN IF NOT EXISTS exit_day_close_r numeric,
    ADD COLUMN IF NOT EXISTS phantom_stop boolean;
