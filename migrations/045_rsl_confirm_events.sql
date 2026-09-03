-- 045: the confirmed-runner cut of the RS-leader GO (2026-09-03 AM —
-- Eric: "I thought we were entering these when the tape is confirming a
-- runner"). Confirmation legs at the GO bar + hold-to-close outcomes +
-- a MODELED 0.70-delta call P&L. Applied live via MCP.

CREATE TABLE IF NOT EXISTS rsl_confirm_events (
    event_id    bigint PRIMARY KEY REFERENCES rs_leader_events(id),
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    legs        jsonb NOT NULL,
    outcomes    jsonb NOT NULL
);
