-- Exact wall-clock timestamp of when each alert first fired.
-- alert_date alone can't distinguish a 9:40 alert from a 3:50 one,
-- which matters for judging actionability and time-of-day edge.
-- (Applied to Supabase 2026-06-10 via MCP apply_migration.)
ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS alerted_at TIMESTAMPTZ DEFAULT now();

UPDATE alert_log SET alerted_at = alert_date::timestamptz WHERE alerted_at IS NULL;
