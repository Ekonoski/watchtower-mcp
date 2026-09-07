-- 058: the Beat-SPY challenge harness (2026-09-07). Runs, daily equity
-- vs SPY total return, and the trade list per run. A sealed-window run
-- is written once per name and the code refuses to write it twice.
-- beat_spy_dividends caches SPY cash dividends by ex-date for the
-- total-return benchmark. Applied live via MCP.

CREATE TABLE IF NOT EXISTS beat_spy_runs (
    id           serial PRIMARY KEY,
    name         text NOT NULL,
    run_window   text NOT NULL CHECK (run_window IN ('build', 'sealed')),
    params       jsonb NOT NULL,
    start_date   date NOT NULL,
    end_date     date NOT NULL,
    final_equity numeric,
    cagr         numeric,
    max_dd       numeric,
    spy_final    numeric,
    spy_cagr     numeric,
    spy_max_dd   numeric,
    n_trades     integer,
    wins         integer,
    beats        boolean,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, run_window)
);
CREATE TABLE IF NOT EXISTS beat_spy_equity (
    run_id  integer NOT NULL REFERENCES beat_spy_runs(id) ON DELETE CASCADE,
    d       date NOT NULL,
    equity  numeric NOT NULL,
    spy_tr  numeric NOT NULL,
    PRIMARY KEY (run_id, d)
);
CREATE TABLE IF NOT EXISTS beat_spy_trades (
    id         bigserial PRIMARY KEY,
    run_id     integer NOT NULL REFERENCES beat_spy_runs(id) ON DELETE CASCADE,
    sleeve     text NOT NULL,
    ticker     text NOT NULL,
    entry_date date, entry_px numeric,
    exit_date  date, exit_px numeric,
    qty        numeric, pnl numeric,
    reason     text
);
CREATE INDEX IF NOT EXISTS idx_beat_spy_trades_run ON beat_spy_trades (run_id);
CREATE TABLE IF NOT EXISTS beat_spy_dividends (
    ticker  text NOT NULL,
    ex_date date NOT NULL,
    amount  numeric NOT NULL,
    PRIMARY KEY (ticker, ex_date)
);
