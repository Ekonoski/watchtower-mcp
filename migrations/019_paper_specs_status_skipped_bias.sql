-- 019: Admit 'skipped_bias' to paper_specs' status allowlist
-- (2026-08-24, applied to live DB same day, minutes after 018).
--
-- Second gate of the same debut morning: after 018 admitted the
-- day_bias BOOK, the STATUS check rejected its stand-aside verdict.
-- The full audit this time — all check constraints on paper_specs:
--   book      : fixed in 018 (day_bias added)
--   direction : long/short — fine (the book is long-only)
--   status    : this migration (skipped_bias added; the loop writes
--               armed / triggered / cancelled / skipped_bias, all now
--               admitted)
-- The lesson, twice in one morning: when a new book arrives, audit
-- EVERY allowlist the row must pass through, in one pass — a gate
-- checked one bounce at a time is a morning spent bouncing.

ALTER TABLE paper_specs DROP CONSTRAINT paper_specs_status_check;
ALTER TABLE paper_specs ADD CONSTRAINT paper_specs_status_check
  CHECK (status = ANY (ARRAY['armed'::text, 'triggered'::text,
                             'expired'::text, 'cancelled'::text,
                             'skipped_binary'::text, 'skipped_bias'::text]));
