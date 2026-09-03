-- 048: the day-state conditioning grid (2026-09-03). One row per graded
-- entry (and per SPY/QQQ day-bias day) with the day-states knowable by
-- 9:45 ET; outcomes NULL for GO/tape rows (joined from riskmgmt_events
-- at readout), filled for day-bias days. Applied live via MCP.

CREATE TABLE IF NOT EXISTS daystate_legs (
    source      text NOT NULL,          -- rsl_go / tapeentry / daybias
    event_id    bigint NOT NULL,
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    legs        jsonb NOT NULL,
    outcomes    jsonb,
    PRIMARY KEY (source, event_id)
);
CREATE INDEX IF NOT EXISTS daystate_legs_date ON daystate_legs (trade_date);
