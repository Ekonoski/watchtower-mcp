-- The live watchlist table predated migration 001 and lacked the `active`
-- flag — every `WHERE active = true` read (scans, news known-tickers,
-- dashboard) silently failed, and watchlist adds 500'd.
-- (Applied to Supabase 2026-06-11 via MCP apply_migration.)
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT true;
UPDATE watchlist SET active = true WHERE active IS NULL;
