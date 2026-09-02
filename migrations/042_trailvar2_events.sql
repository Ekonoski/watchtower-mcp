-- 042: trail-variant extension II (2026-09-02, research docket: Kalman
-- trail #11 and MAD volatility trail P2-21), re-simulated on the same
-- graded ema_1m_gated entries as hybridexit/trailvar. Applied live via
-- MCP the same evening.

CREATE TABLE IF NOT EXISTS trailvar2_events (
    event_id    bigint PRIMARY KEY REFERENCES tapeentry_events(id),
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    variants    jsonb NOT NULL
);
