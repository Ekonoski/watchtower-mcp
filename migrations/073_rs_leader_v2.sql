-- 073: rs_leader v2 is born into the schema (2026-09-15, Eric on the
-- desk's META trade — +3.0R at the high, trailed out −2.01R: "a plus
-- three R to negative two... should be considered unacceptable, that's
-- poor trade management" → "build that"). Same GO entry; the exit is
-- his rule: half off at the first level above entry, the runner's stop
-- to entry on touch, ratcheted under each completed 5m low, the rest at
-- the second level or the bell. A different trade gets a new book name
-- and its own n (the 8/23 harness doctrine); v1's record stands.
--
-- Book birth touches every allowlist at once (the 2026-09-01 lesson —
-- three silent refusals in one morning):
--   paper_specs.book        + 'rs_leader_v2'
--   paper_specs.status      + 'skipped_rank'  (v1 wrote this status on a
--                             stand-aside day and it was NEVER admitted —
--                             no stand-aside day has happened in ten
--                             sessions, so the refusal never fired; the
--                             assert-admission lesson at the schema layer)
--   paper_trades.exit_reason + tp1_be / tp1_ratchet / tp1_tp2 / tp1_eod
--                             (the whole-trade reason once half is banked:
--                             what took the RUNNER out)
--   paper_trades.legs        jsonb — every partial fill with its price,
--                             minute and reason, so the weighted exit_px
--                             is auditable from the row forever
--   paper_specs.levels       jsonb — TP1/TP2 and their kinds, frozen at
--                             the GO (PDH / PMH / ORB high / pre-GO session
--                             high; holes named), so the target the book
--                             traded is the target the row shows.
-- Applied live via MCP.

ALTER TABLE paper_specs DROP CONSTRAINT IF EXISTS paper_specs_book_check;
ALTER TABLE paper_specs ADD CONSTRAINT paper_specs_book_check
  CHECK (book = ANY (ARRAY['gamma'::text, 'gamma_iday'::text,
                           'swing'::text, 'swing_v2'::text,
                           'day_bias'::text, 'rs_leader'::text,
                           'rs_leader_v2'::text]));

ALTER TABLE paper_specs DROP CONSTRAINT IF EXISTS paper_specs_status_check;
ALTER TABLE paper_specs ADD CONSTRAINT paper_specs_status_check
  CHECK (status = ANY (ARRAY['armed'::text, 'triggered'::text,
                             'expired'::text, 'cancelled'::text,
                             'skipped_binary'::text, 'skipped_bias'::text,
                             'skipped_rank'::text]));

ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_exit_reason_check;
ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_exit_reason_check
  CHECK (exit_reason = ANY (ARRAY['target'::text, 'stop'::text,
                                  'clock_1430'::text, 'eod_flat'::text,
                                  'binary_gate'::text, 'manual'::text,
                                  'trail'::text, 'disaster'::text,
                                  'tp1_be'::text, 'tp1_ratchet'::text,
                                  'tp1_tp2'::text, 'tp1_eod'::text]));

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS legs jsonb;
ALTER TABLE paper_specs ADD COLUMN IF NOT EXISTS levels jsonb;
