-- 050: the journal carries BOTH R readings (2026-09-03, Eric: "so you
-- are logging my actual +R and the +R against the $250?"). r_multiple
-- stays the $250-baseline yardstick; r_actual is the trade's own R on
-- its stop-defined dollar risk (risk_dollars). NULL = the stop or size
-- is unknown — a hole, never a zero. Applied live via MCP.

ALTER TABLE trade_journal
    ADD COLUMN IF NOT EXISTS risk_dollars numeric,
    ADD COLUMN IF NOT EXISTS r_actual numeric;
COMMENT ON COLUMN trade_journal.r_multiple IS 'R against the $250 baseline (the desk yardstick)';
COMMENT ON COLUMN trade_journal.r_actual IS 'R against the trade''s own stop-defined dollar risk (risk_dollars); NULL = stop or size unknown (a hole, never a zero)';
