-- 015: Sector rotation — measured before it gates (2026-08-22).
--
-- Eric: "you can pick a great stock but be in the wrong sector while
-- money rotation is flowing out." The system SEES rotation (heatmap,
-- rotation tracking) but the trade engine never asked about it. Per
-- doctrine the answer is measurement first: a breadth-style sector
-- relative-strength cache (median stock vs market median, from our own
-- daily_prices), a per-episode historical study over the graded daily
-- bullish record, and a measurement-only sector_state tag on swing
-- specs (the osc_state pattern — stamped after curation, never read by
-- arming). Promotion, if earned, comes at sample size — likely as a
-- veto/ranking penalty, not a chase signal.

CREATE TABLE IF NOT EXISTS sector_rs_daily (
    trade_date  date    NOT NULL,
    sector      text    NOT NULL,
    rs_1m       numeric NOT NULL,   -- sector median 21d return - market median
    rs_1w       numeric NOT NULL,   -- same, 5d
    rank_1m     int     NOT NULL,   -- 1 = strongest sector that day
    n_tickers   int     NOT NULL,
    PRIMARY KEY (trade_date, sector)
);

CREATE TABLE IF NOT EXISTS sector_study (
    episode_id    bigint  PRIMARY KEY,  -- pattern_backtest.id
    ticker        text    NOT NULL,
    sector        text,                 -- NULL = no mapping (a hole, kept)
    breakout_date date,
    pattern       text,
    rs_1m         numeric,              -- NULL = cache hole (kept)
    rs_1w         numeric,
    rank_1m       int,
    win_1r        boolean,
    realized_r    numeric,
    outcome       text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE paper_specs ADD COLUMN IF NOT EXISTS sector_state jsonb;

ALTER TABLE sector_rs_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE sector_study    ENABLE ROW LEVEL SECURITY;
