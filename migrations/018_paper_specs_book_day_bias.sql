-- 018: Admit the day_bias book (2026-08-24, applied to live DB same day).
--
-- The day-bias book's first live morning: the loop ran, the logic was
-- right, the decision was right (skipped_bias — SPY opened 764.78,
-- below PDH 767.85), and every insert bounced off paper_specs'
-- book-name allowlist, written before the book existed. Nothing
-- reached the record until the error itself was routed into
-- ingestion_log (PR #232) — a failure that only stdout knows about is
-- a hole in the record. This is the assert-admission lesson at the
-- schema layer: a check constraint is a gate, and gates must be
-- updated when a new class of thing earns entry.

ALTER TABLE paper_specs DROP CONSTRAINT paper_specs_book_check;
ALTER TABLE paper_specs ADD CONSTRAINT paper_specs_book_check
  CHECK (book = ANY (ARRAY['gamma'::text, 'gamma_iday'::text,
                           'swing'::text, 'day_bias'::text]));
