-- 044: the RS-leader EXIT re-grade on the book's own GO entries
-- (2026-09-02 evening — the chase study's f=0 baseline showed the live
-- lifecycle grading NEGATIVE on the 446 graded GO entries while
-- hold-to-close on the same entries grades positive; the exits had
-- been graded on the tape-entry population only). One row per graded
-- GO; declared exit variants side by side. Applied live via MCP.

CREATE TABLE IF NOT EXISTS rsl_exit_events (
    event_id    bigint PRIMARY KEY REFERENCES rs_leader_events(id),
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    variants    jsonb NOT NULL
);
