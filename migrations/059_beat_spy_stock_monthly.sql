-- 059: Beat-SPY v8 — the single-name momentum sleeve's monthly ranking
-- table. One row per (SPY month-end date, ticker) that was LIQUID AT THAT
-- DATE (60-bar average dollar volume >= $10M, close >= $5) with a full
-- 252-bar history: the 21- and 252-bar-ago closes for 12-1 momentum.
-- Derived from daily_prices by the app's one-shot seeder (window functions
-- over the whole table are too heavy for the MCP statement timeout).
-- Survivorship stated: daily_prices holds currently-listed names plus the
-- delisted rows the nightly jobs kept; the universe is "liquid then", not
-- "liquid today". Applied live via MCP.

CREATE TABLE IF NOT EXISTS beat_spy_stock_monthly (
    me_date  date NOT NULL,
    ticker   text NOT NULL,
    close    numeric NOT NULL,
    c21      numeric NOT NULL,
    c252     numeric NOT NULL,
    dv60     numeric NOT NULL,
    PRIMARY KEY (me_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_bs_stock_monthly_date ON beat_spy_stock_monthly (me_date);
