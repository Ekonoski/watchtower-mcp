-- 043: the chase-premium tolerance grade (2026-09-02, pre-registered —
-- how far above the GO close is the RS-leader trade still the trade?).
-- One row per graded leader GO; fills keyed by premium fraction of risk.
-- Applied live via MCP the same evening.

CREATE TABLE IF NOT EXISTS chase_events (
    event_id    bigint PRIMARY KEY REFERENCES rs_leader_events(id),
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    fills       jsonb NOT NULL      -- {"0.00": {...}, "0.10": {...}, ...}
);
