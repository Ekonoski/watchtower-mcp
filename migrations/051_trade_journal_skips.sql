-- 051: the journal records SKIPS (2026-09-04, Eric, on the NVDA GO he
-- passed on: "the skip is data, and the reason for the skip is also
-- data. So I think that needs to be journaled"). A skip row is a
-- DECISION with a stated reason, never a trade: kind='skip', no P&L,
-- no R — it is excluded from every R aggregate by kind and rendered in
-- its own section. spec_id links the skip to the desk alert it
-- declined (paper_specs.id) so the book's own outcome on that alert
-- grades the eye against the machine. Applied live via MCP.

ALTER TABLE trade_journal
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'trade',
    ADD COLUMN IF NOT EXISTS skip_reason text,
    ADD COLUMN IF NOT EXISTS spec_id bigint REFERENCES paper_specs(id) ON DELETE SET NULL;
ALTER TABLE trade_journal DROP CONSTRAINT IF EXISTS trade_journal_kind_chk;
ALTER TABLE trade_journal
    ADD CONSTRAINT trade_journal_kind_chk CHECK (kind IN ('trade', 'skip'));
ALTER TABLE trade_journal DROP CONSTRAINT IF EXISTS trade_journal_skip_no_r_chk;
ALTER TABLE trade_journal
    ADD CONSTRAINT trade_journal_skip_no_r_chk
    CHECK (kind <> 'skip' OR (pnl_dollars IS NULL AND r_multiple IS NULL AND r_actual IS NULL));
COMMENT ON COLUMN trade_journal.kind IS 'trade = a position taken; skip = an alert/setup declined with a stated reason (a decision, never an R)';
COMMENT ON COLUMN trade_journal.skip_reason IS 'why the trade was not taken, in Eric''s words (skips only)';
COMMENT ON COLUMN trade_journal.spec_id IS 'the desk spec (paper_specs.id) this row took or declined, when it was a desk alert — lets the book''s outcome grade the eye';
