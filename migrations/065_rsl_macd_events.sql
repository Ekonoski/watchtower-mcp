-- 065: the MACD-extension leg of the RS-leader GO (2026-09-09 evening —
-- Eric skipped the TSLA 🎯 because MACD sat above zero on the 1m/5m/15m;
-- the desk lost −1.77R on it). MACD(12,26,9) at the GO bar on three
-- timeframes (continuous RTH series, completed blocks only) beside the
-- hold-to-close and live-book outcomes. Applied live via MCP.

CREATE TABLE IF NOT EXISTS rsl_macd_events (
    event_id    bigint PRIMARY KEY REFERENCES rs_leader_events(id),
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    macd        jsonb NOT NULL,
    n_above     smallint,
    extended    boolean,
    outcomes    jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS rsl_macd_events_tk_d ON rsl_macd_events (ticker, trade_date);
