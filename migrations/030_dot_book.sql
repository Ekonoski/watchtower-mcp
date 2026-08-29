-- 030: The dot book — Eric's LIVE swing ledger (2026-08-29). Every
-- position records its entry, its signal anchor (the dot it rides,
-- when it has one), its planned tranche levels, and its exit line —
-- so the live sleeve grades against the study priors the same way
-- every paper book grades against its backtest. An entry price the
-- ledger doesn't have renders as a hole, never a guess.

CREATE TABLE IF NOT EXISTS dot_book (
    id            serial PRIMARY KEY,
    ticker        text NOT NULL,
    entry_kind    text NOT NULL,     -- at_dot | near_dot | reclaim_premium
                                     -- | runner_hold | structure
    entry_date    date,
    entry_px      numeric,           -- NULL until Eric supplies the fill
    size_note     text,              -- free text; sizing is Eric's
    dot_date      date,              -- signal anchor (greendot_dots), if any
    dot_px        numeric,
    cross_depth   numeric,
    drawdown_pct  numeric,
    add1_px       numeric,           -- planned tranche levels, if laddering
    add2_px       numeric,
    stop_line     text,              -- e.g. 'daily close < 546'
    thesis        text,
    status        text NOT NULL DEFAULT 'open',
    exit_date     date,
    exit_px       numeric,
    exit_reason   text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- One OPEN row per ticker; closed history unlimited.
CREATE UNIQUE INDEX IF NOT EXISTS idx_dot_book_open
    ON dot_book (ticker) WHERE status = 'open';
