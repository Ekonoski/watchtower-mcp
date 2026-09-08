-- 063: the day-type study (2026-09-08, Eric: "we need to identify chop vs a
-- red or green day"). One row per index day: the day's LABEL at the close
-- (trend_green / trend_red / chop / mixed, from range vs ATR20 and close
-- position) beside what was READABLE at 9:45, 10:00 and 10:30 with no
-- lookahead. Applied live via MCP.

CREATE TABLE IF NOT EXISTS daytype_days (
    ticker       text NOT NULL,
    trade_date   date NOT NULL,
    era          text NOT NULL,
    label        text NOT NULL,
    range_ratio  numeric,
    close_pos    numeric,
    day_ret_pct  numeric,
    f945         jsonb,
    f1000        jsonb,
    f1030        jsonb,
    PRIMARY KEY (ticker, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daytype_label ON daytype_days (ticker, label);

CREATE TABLE IF NOT EXISTS daytype_progress (
    ticker   text PRIMARY KEY,
    n_days   integer NOT NULL DEFAULT 0,
    done_at  timestamptz NOT NULL DEFAULT now()
);
