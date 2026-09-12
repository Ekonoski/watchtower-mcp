-- 072: the sector-leader study (2026-09-12 — the X post's "find the
-- sector, find the leader, wait for the break and retest", graded on the
-- daily bullish episode record). One row per episode: the sector ETF's
-- strength legs and the stock's leader legs at the PRIOR close (no
-- lookahead), fund rows and warmup legs as NULL holes, the peer
-- percentile stamped by a per-sector cross-section in stage 2.
-- Applied live via MCP.

CREATE TABLE IF NOT EXISTS sector_leader_events (
    episode_id     bigint PRIMARY KEY REFERENCES pattern_backtest(id) ON DELETE CASCADE,
    ticker         text NOT NULL,
    sector         text,
    etf            text,
    fund           boolean NOT NULL DEFAULT false,
    breakout_date  date NOT NULL,
    prior_date     date,
    era            text NOT NULL,
    pattern        text,
    retest_bar     smallint,
    win_1r         boolean,
    realized_r     numeric,
    etf_rs21       numeric,
    etf_rs63       numeric,
    etf_rank21     smallint,
    n_etfs         smallint,
    etf_above      boolean,
    etf_near_hi    boolean,
    etf_beats_qqq  boolean,
    stk_ret21      numeric,
    stk_ret63      numeric,
    stk_rs_etf21   numeric,
    stk_rs_spy21   numeric,
    stk_above      boolean,
    stk_near_hi    boolean,
    vol_exp        numeric,
    green_red10    smallint,
    defect         boolean NOT NULL DEFAULT false,
    pct21          numeric,
    pct63          numeric,
    n_peers        integer,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sector_leader_events_sector_prior ON sector_leader_events (sector, ticker, prior_date);

CREATE TABLE IF NOT EXISTS sector_leader_progress (
    ticker      text PRIMARY KEY,
    n_events    integer NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
