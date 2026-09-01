-- 037: book-birth allowlist extensions (APPLIED LIVE 2026-09-01,
-- mid-session, during the rs_leader book's first day — recorded here
-- so the repo matches the database). Three silent refusals in one
-- day, all the same disease: schema allowlists written for the
-- incumbent books rejected the new book's writes with no error
-- surfacing anywhere. The nightly ledger audit (analysis/
-- ledger_audit.py) now checks the exit-reason vocabulary per book
-- every evening.

ALTER TABLE paper_specs DROP CONSTRAINT IF EXISTS paper_specs_book_check;
ALTER TABLE paper_specs ADD CONSTRAINT paper_specs_book_check
  CHECK (book = ANY (ARRAY['gamma'::text, 'gamma_iday'::text,
                           'swing'::text, 'day_bias'::text,
                           'rs_leader'::text]));

ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_fill_kind_chk;
ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_fill_kind_chk
  CHECK (fill_kind = ANY (ARRAY['touch'::text, 'reclaim'::text,
                                'close_through'::text, 'close'::text]));

ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_exit_reason_check;
ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_exit_reason_check
  CHECK (exit_reason = ANY (ARRAY['target'::text, 'stop'::text,
                                  'clock_1430'::text, 'eod_flat'::text,
                                  'binary_gate'::text, 'manual'::text,
                                  'trail'::text, 'disaster'::text]));
