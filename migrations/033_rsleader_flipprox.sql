-- 033: Two frequency studies (2026-09-01, the night after the QQQ chop
-- beating — Eric: "there is a way to find more trades than just what
-- we have... and be successful" / "yes run the RS-leader study tonight").
--
-- rs-leader: the TSLA trade, pre-registered — morning relative-strength
-- leader vs QQQ, first 1m pullback holding the 1m 8/21 (wick rule),
-- bracket outcomes from the 1m tape. Laggard short mirror recorded.
--
-- flip-proximity: does the open's distance to the gamma flip predict
-- chop character? Graded from RECORDED boards + stored 15m bars.

CREATE TABLE IF NOT EXISTS mag7_1m_bars (
    ticker      text NOT NULL,
    ts          timestamptz NOT NULL,
    trade_date  date NOT NULL,
    open        numeric NOT NULL,
    high        numeric NOT NULL,
    low         numeric NOT NULL,
    close       numeric NOT NULL,
    volume      numeric,
    PRIMARY KEY (ticker, ts)
);
CREATE INDEX IF NOT EXISTS idx_mag7_1m_tk_date ON mag7_1m_bars (ticker, trade_date);

CREATE TABLE IF NOT EXISTS rs_leader_events (
    id            bigserial PRIMARY KEY,
    trade_date    date NOT NULL,
    ticker        text NOT NULL,
    role          text NOT NULL,          -- leader / laggard / midpack_baseline
    direction     text NOT NULL,          -- long / short
    rs_945_pct    numeric NOT NULL,       -- return-from-open minus QQQ's, at 9:45
    entry_kind    text NOT NULL,          -- go_pullback / no_pullback_945
    entry_ts      timestamptz,
    entry_px      numeric,
    stop_px       numeric,
    outcome       text,                   -- target2 / target1 / stopped / eod_flat / no_entry
    r_first       numeric,                -- bracket result in R (first-touch, wick-rule closes for stop)
    r_noon        numeric,
    r_close       numeric,
    mfe_r         numeric,
    mae_r         numeric,
    UNIQUE (trade_date, ticker, role, entry_kind)
);

CREATE TABLE IF NOT EXISTS flipprox_days (
    id            bigserial PRIMARY KEY,
    ticker        text NOT NULL,
    trade_date    date NOT NULL,
    open_px       numeric NOT NULL,
    flip_px       numeric,                -- NULL = board hole, recorded as such
    dist_pct      numeric,                -- |open-flip|/open * 100
    flip_crosses  integer,                -- 15m closes crossing the flip
    pdh_pdl_touch integer,                -- how many of the two prior-day levels were touched
    range_travel  numeric,                -- day range / sum(|15m close moves|): low = chop
    close_ret_bps numeric,
    UNIQUE (ticker, trade_date)
);
