-- 076: fills_audit — the live fill-provenance ledger (plural).
-- Distinct from fill_audit (singular, 2026-08-27 forensic Q&A on two
-- questioned SPY fills). Every paper_trades INSERT / exit UPDATE writes
-- one row here in the SAME transaction; a failed audit insert rolls the
-- trade write back. Applied live via MCP. No RLS enable-blind (a new
-- table with RLS and no policies is deny-all even to the service role
-- depending on FORCE; this ledger is written over DATABASE_URL).

CREATE TABLE IF NOT EXISTS fills_audit (
    id              bigserial PRIMARY KEY,
    paper_trade_id  bigint NOT NULL REFERENCES paper_trades(id),
    book            text NOT NULL,
    ticker          text NOT NULL,
    event           text NOT NULL CHECK (event IN ('entry', 'exit')),
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
    UNIQUE (paper_trade_id, event)
);

CREATE INDEX IF NOT EXISTS idx_fills_audit_book_event
    ON fills_audit (book, event, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fills_audit_trade
    ON fills_audit (paper_trade_id);
