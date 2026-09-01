-- 036: the RS-leader audition book (2026-08-31 late, Eric: "Yes go
-- build the paper book"). Its own 1m bar record — decisions read only
-- persisted bars (reconstruction is not tape), first-seen wins.

CREATE TABLE IF NOT EXISTS rsl_book_bars (
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
CREATE INDEX IF NOT EXISTS idx_rsl_book_bars_date ON rsl_book_bars (ticker, trade_date);
