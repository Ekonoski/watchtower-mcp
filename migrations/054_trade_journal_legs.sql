-- 054: the journal's LEGS (2026-09-04, Eric: "That list works, build it
-- this weekend"). Every row — trade or skip — carries the tags for the
-- reasons the eye used, from the fixed vocabulary in
-- analysis/journal_legs.py (one definition; the code refuses unknown
-- tags at the door). At ~30 rows the grade is per leg: win rate and R
-- with vs without each tag — the cipher-museum mechanism applied to
-- Eric's own book. Applied live via MCP.

ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS legs text[];
COMMENT ON COLUMN trade_journal.legs IS 'the reasons the eye used, as tags from the fixed vocabulary in analysis/journal_legs.py (entry / context / exit / skip); graded per tag at ~30 rows';
