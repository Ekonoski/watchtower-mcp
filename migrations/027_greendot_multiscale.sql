-- 027: The multiscale dot study — the SAME below-zero dot definition
-- on daily and weekly bars (16D already recorded in greendot_dots),
-- outcomes at fixed daily horizons so per-scale compounding rates
-- compare on one ruler. Eric's question: quick daily bounces, weekly
-- bounces, or long 16D holds — which grows the account fastest?

CREATE TABLE IF NOT EXISTS greendot_dots_ms (
    id              serial PRIMARY KEY,
    scale           text NOT NULL,       -- daily | weekly
    ticker          text NOT NULL,
    dot_date        date NOT NULL,
    cross_depth     numeric,
    drawdown_pct    numeric,
    dd_bucket       text,
    px_at_dot       numeric,
    dist_low63_pct  numeric,
    fwd_21d_pct     numeric,
    fwd_63d_pct     numeric,
    fwd_126d_pct    numeric,
    fwd_252d_pct    numeric,
    era             text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scale, ticker, dot_date)
);

CREATE INDEX IF NOT EXISTS idx_greendot_ms_cohort
    ON greendot_dots_ms (scale, dd_bucket);

CREATE TABLE IF NOT EXISTS greendot_ms_progress (
    scale       text NOT NULL,
    ticker      text NOT NULL,
    n_dots      integer,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scale, ticker)
);
