-- 041: the wall-touch prior (2026-09-02, pre-registered — "how often
-- does each morning level actually get touched by the close?").
-- One row per (ticker, day, level kind) from the day's FIRST intraday
-- board; touched NULL = bars unavailable, a hole never a miss.
-- Applied live via MCP the same evening.

CREATE TABLE IF NOT EXISTS wall_touch_events (
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    kind        text NOT NULL,          -- call_wall / put_wall / gamma_flip
    board_ts    timestamptz NOT NULL,   -- the first intraday board of the day
    level       numeric NOT NULL,
    spot        numeric NOT NULL,
    dist_pct    numeric NOT NULL,       -- (spot - level) / level * 100, signed
    regime      text,
    touched     boolean,                -- NULL = bars unavailable (hole)
    touch_ts    timestamptz,
    touched_1h  boolean,
    bars_source text,                   -- index_15m / mag7_1m / none
    PRIMARY KEY (ticker, trade_date, kind)
);
