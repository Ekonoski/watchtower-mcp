-- 046: risk management as the edge (2026-09-03 AM — Eric: "isn't the
-- bigger edge in risk management?"). Asymmetric exits with the risk
-- unit at the real excursion, graded on the coin-flip populations.
-- Applied live via MCP.

CREATE TABLE IF NOT EXISTS riskmgmt_events (
    source      text NOT NULL,          -- tapeentry / rsl_go
    event_id    bigint NOT NULL,
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    variants    jsonb NOT NULL,
    PRIMARY KEY (source, event_id)
);
