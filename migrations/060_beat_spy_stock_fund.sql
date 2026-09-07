-- 060: Beat-SPY v9 — point-in-time fundamentals per (SPY month-end, ticker)
-- for the single-name universe in beat_spy_stock_monthly. Trailing-four-
-- quarter sums built ONLY from filings whose report_date <= me_date (the
-- earliest filing per period, so a restatement filed later cannot leak
-- back). n4/n8 count the quarters available; last_report is the newest
-- filing date used. Seeded once by the app; applied live via MCP.

CREATE TABLE IF NOT EXISTS beat_spy_stock_fund (
    me_date       date NOT NULL,
    ticker        text NOT NULL,
    ttm_ni        numeric,
    ttm_ocf       numeric,
    ttm_rev       numeric,
    ttm_rev_prev  numeric,
    n4            integer NOT NULL,
    n8            integer NOT NULL,
    last_report   date,
    PRIMARY KEY (me_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_fq_ticker_report ON fundamentals_quarterly (ticker, report_date);
