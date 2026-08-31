-- 035: hybrid-exit study (2026-08-31 late — Eric: "Why can't we do the
-- hybrid test now?"). Exit variants re-simulated on the tape-entry
-- study's own graded entries; one row per source event, variants JSONB.

CREATE TABLE IF NOT EXISTS hybridexit_events (
    event_id    bigint PRIMARY KEY REFERENCES tapeentry_events(id),
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    variants    jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hybridexit_date ON hybridexit_events (trade_date);
