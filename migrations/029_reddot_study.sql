-- 029: The 16D RED-dot study — the green dot's mirror. Bearish
-- wavetrend cross-downs above the zero line, cross height stored
-- (elevation is a readout cut, never baked in), run-up vs the
-- trailing 2-year low as the condition, forward returns plus
-- distance to the forward 6-month high (the cost of exiting) as
-- outcomes. NOT a short signal — the candidate EXIT rule for the
-- dot sleeve and top-confirmation on holds.

CREATE TABLE IF NOT EXISTS reddot_dots (
    id                serial PRIMARY KEY,
    ticker            text NOT NULL,
    dot_date          date NOT NULL,
    cross_height      numeric,          -- wt2 at the cross (>= 0 by def)
    runup_pct         numeric,          -- close vs trailing 2y low
    ru_bucket         text,             -- lt50 | b50_100 | b100_200 | gte200
    px_at_dot         numeric,
    dist_to_high_pct  numeric,          -- fwd 6-mo high vs dot close
    fwd_63d_pct       numeric,
    fwd_126d_pct      numeric,
    fwd_252d_pct      numeric,
    era               text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticker, dot_date)
);

CREATE INDEX IF NOT EXISTS idx_reddot_cohort
    ON reddot_dots (ru_bucket, era);

CREATE TABLE IF NOT EXISTS reddot_progress (
    ticker      text PRIMARY KEY,
    n_dots      integer,
    created_at  timestamptz NOT NULL DEFAULT now()
);
