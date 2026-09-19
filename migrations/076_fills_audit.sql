-- 076: fills_audit — the live fill-provenance ledger (plural).
-- Distinct from fill_audit (singular, 2026-08-27 forensic Q&A on two
-- questioned SPY fills). Every paper_trades INSERT / exit UPDATE writes
-- ledger rows here in the SAME transaction; failures roll back the trade.
-- Run before jobs start (ensure_schema); historical backfill is opt-in.
-- No grants or RLS changes. Singular fill_audit is untouched.

CREATE TABLE IF NOT EXISTS fills_audit (
    id              bigserial PRIMARY KEY,
    paper_trade_id  bigint NOT NULL REFERENCES paper_trades(id),
    book            text NOT NULL,
    ticker          text NOT NULL,
    event           text NOT NULL CHECK (event IN ('entry', 'exit', 'leg')),
    fill_kind       text,
    expected_px     numeric,
    got_px          numeric NOT NULL,
    gap_through     boolean NOT NULL DEFAULT false,
    phantom_stop    boolean NOT NULL DEFAULT false,
    bar             jsonb,
    evidence        jsonb,
    provenance      text NOT NULL DEFAULT 'live'
                    CHECK (provenance IN ('live', 'backfill_inferred')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    leg_index       integer NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_fills_audit_book_event
    ON fills_audit (book, event, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fills_audit_trade
    ON fills_audit (paper_trade_id);

-- Upgrade databases that received the earlier entry/exit-only draft.
ALTER TABLE fills_audit ADD COLUMN IF NOT EXISTS leg_index integer NOT NULL DEFAULT 0;
ALTER TABLE fills_audit DROP CONSTRAINT IF EXISTS fills_audit_event_check;
ALTER TABLE fills_audit ADD CONSTRAINT fills_audit_event_check
    CHECK (event IN ('entry', 'exit', 'leg'));
ALTER TABLE fills_audit DROP CONSTRAINT IF EXISTS fills_audit_paper_trade_id_event_key;
ALTER TABLE fills_audit DROP CONSTRAINT IF EXISTS fills_audit_leg_index_check;
ALTER TABLE fills_audit ADD CONSTRAINT fills_audit_leg_index_check
    CHECK ((event = 'leg' AND leg_index > 0) OR (event <> 'leg' AND leg_index = 0));
CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_audit_event_leg
    ON fills_audit (paper_trade_id, event, leg_index);
