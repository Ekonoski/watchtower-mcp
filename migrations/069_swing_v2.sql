-- 069: swing v2 is born into the schema (2026-09-10, Eric: "at some point
-- we need to make decisions on where to go"). The book-level gate set on
-- 2026-08-10 (profit at ~30 resolved / ~2 months) was reached and failed
-- — 33 resolved, -24.5R — so v1 stops arming; v2 arms on CLOSE-CONFIRMED
-- entries with the two negative-prior daily neckline classes retired.
-- New book name 'swing_v2' keeps the ledgers separate; fill_kind 'confirm'
-- names the v2 entry mechanism (a touch, then the first completed 15m bar
-- closing back through the trigger, at that close). Applied live via MCP.

ALTER TABLE paper_specs DROP CONSTRAINT IF EXISTS paper_specs_book_check;
ALTER TABLE paper_specs ADD CONSTRAINT paper_specs_book_check
  CHECK (book = ANY (ARRAY['gamma'::text, 'gamma_iday'::text,
                           'swing'::text, 'swing_v2'::text,
                           'day_bias'::text, 'rs_leader'::text]));

ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_fill_kind_chk;
ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_fill_kind_chk
  CHECK (fill_kind = ANY (ARRAY['touch'::text, 'reclaim'::text,
                                'close_through'::text, 'close'::text,
                                'confirm'::text]));
