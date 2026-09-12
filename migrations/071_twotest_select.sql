-- 071: the selection cut on the two-test engine (2026-09-12 — Eric:
-- "are you doing that, or are you just grabbing names and testing?").
-- One row per triggered twotest event: the Scanner trend gate on
-- 1m/5m/15m/60m at the trigger (NULL = warmup, never 0), the sector's
-- breadth-RS read at the prior close (no sector = hole), the 9:45
-- leader flag. Applied live via MCP.

CREATE TABLE IF NOT EXISTS twotest_select (
    event_id      bigint PRIMARY KEY REFERENCES twotest_events(id) ON DELETE CASCADE,
    trend_1m      smallint,
    trend_5m      smallint,
    trend_15m     smallint,
    trend_60m     smallint,
    n_aligned     smallint,
    aligned       boolean,
    sector        text,
    rank_1m       smallint,
    rs_1w         numeric,
    sector_state  text,
    turning       boolean,
    leader        boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);
