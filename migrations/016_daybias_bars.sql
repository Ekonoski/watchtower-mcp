-- 016: Index intraday history for the day-bias study (2026-08-23).
--
-- Eric: use the 20 years of data to find the daily bias (previous day
-- high/low, prior range) and the best intraday entries. Phase 1 ran on
-- daily bars; phase 2 — first-touch sequencing, retest timing, entry
-- MFE/MAE — needs the intraday tape. SPY/QQQ/IWM 15m RTH bars persist
-- here once (Polygon backfill, analysis/daybias_bars.py) and every
-- entry-model question after that is a repeatable query over our own
-- recorded table.

CREATE TABLE IF NOT EXISTS index_intraday_bars (
    ticker      text        NOT NULL,
    ts          timestamptz NOT NULL,
    trade_date  date        NOT NULL,
    open        numeric     NOT NULL,
    high        numeric     NOT NULL,
    low         numeric     NOT NULL,
    close       numeric     NOT NULL,
    volume      numeric,
    PRIMARY KEY (ticker, ts)
);

CREATE INDEX IF NOT EXISTS idx_iib_ticker_date
    ON index_intraday_bars (ticker, trade_date);

ALTER TABLE index_intraday_bars ENABLE ROW LEVEL SECURITY;
