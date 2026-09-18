-- 074: the two-test book is born into the schema (2026-09-17, Eric —
-- after his third straight correct PLTR skip and the replay showing his
-- preferred entry would have won both days: "build the two-test book").
-- Same GO-book leader, the graded two-test entry (5m close through PDH
-- or the premarket high, then L1 / H / L2 on the 1m, fill on the cross
-- of H), Eric's level exit, the graded hold-to-bell as a shadow.
--   paper_specs.book         + 'two_test'
--   paper_trades.fill_kind   + 'cross'  (the wick-cross fill at H, or the
--                              open when the bar gapped through)
--   paper_trades.shadow      jsonb — counterfactual expressions computed
--                              from the same recorded bars (here the
--                              study's graded hold-to-bell with the
--                              5m-close stop), so the exit choice grades
--                              itself on every trade
-- Exit reasons reuse 073's vocabulary. Applied live via MCP.

ALTER TABLE paper_specs DROP CONSTRAINT IF EXISTS paper_specs_book_check;
ALTER TABLE paper_specs ADD CONSTRAINT paper_specs_book_check
  CHECK (book = ANY (ARRAY['gamma'::text, 'gamma_iday'::text,
                           'swing'::text, 'swing_v2'::text,
                           'day_bias'::text, 'rs_leader'::text,
                           'rs_leader_v2'::text, 'two_test'::text]));

ALTER TABLE paper_trades DROP CONSTRAINT IF EXISTS paper_trades_fill_kind_chk;
ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_fill_kind_chk
  CHECK (fill_kind = ANY (ARRAY['touch'::text, 'reclaim'::text,
                                'close_through'::text, 'close'::text,
                                'confirm'::text, 'cross'::text]));

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS shadow jsonb;
