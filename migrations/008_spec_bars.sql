-- 008: the loop keeps what it sees (2026-08-08).
--
-- Reconstruction is not tape. The TNDM incident: the shadow audit priced a
-- reclaim entry at "18.60" labeled as Friday's real first 15m close back
-- through the trigger — chart verification found no such close printed.
-- The desk's own loop had fetched (or would have fetched) those exact bars
-- and discarded them; every audit since has been archaeology against a
-- vendor instead of a query against the record.
--
-- Every completed 15m bar the trigger loop evaluates for a spec ticker is
-- persisted here, idempotently, as it is seen. Audits, fill verification,
-- and the confirmation shadow replay from THIS table — bars the loop
-- actually decided on — never from refetched history.
--
-- Volume is deliberately absent: every desk decision (fills, stops,
-- reclaims, shadows) is price-only today. Add it the day a rule reads it.

CREATE TABLE IF NOT EXISTS paper_spec_bars (
    ticker      text        NOT NULL,
    ts          timestamptz NOT NULL,   -- bar START (ET session bars)
    open        numeric     NOT NULL,
    high        numeric     NOT NULL,
    low         numeric     NOT NULL,
    close       numeric     NOT NULL,
    trade_date  date        NOT NULL,
    seen_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, ts)
);

CREATE INDEX IF NOT EXISTS paper_spec_bars_date_idx
    ON paper_spec_bars (trade_date, ticker);

ALTER TABLE paper_spec_bars ENABLE ROW LEVEL SECURITY;
