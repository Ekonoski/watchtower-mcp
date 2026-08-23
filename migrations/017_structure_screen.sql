-- 017: The structure screen (2026-08-23).
--
-- Eric: "Can we find the major levels automatically and screen for
-- names?" The levels engine's multi-touch shelves, run fleet-wide off
-- recorded daily bars, with the break -> retest -> failed lifecycle
-- classified at MAJOR (>=3-touch) levels only. Shelves are computed
-- from bars BEFORE the action window (no lookahead); closes decide
-- breaks and failures (wick rule); bearish rows are warnings, never
-- entry candidates (shorts retired). A screen, graded by forward
-- returns — never wired into arming.

CREATE TABLE IF NOT EXISTS structure_screen (
    run_date    date    NOT NULL,
    ticker      text    NOT NULL,
    direction   text    NOT NULL,   -- bullish (resistance break) | bearish (support breakdown, warning-only)
    state       text    NOT NULL,   -- breakout | retest | failed
    level       numeric NOT NULL,
    touches     int     NOT NULL,
    stars       int,
    timeframes  text,
    break_date  date,
    retest_date date,
    last_close  numeric,
    dist_pct    numeric,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_date, ticker, level, direction)
);

ALTER TABLE structure_screen ENABLE ROW LEVEL SECURITY;
