-- 026: The green-dot robustness pass — same spec at 15-day blocks
-- (the 3W-chart equivalent). If the dot edge is ~3-week structure it
-- survives re-blocking; if it only exists at exactly 16 days it is
-- curve-fit. Separate tables so the 16D record is never touched.

CREATE TABLE IF NOT EXISTS greendot_dots15 (
    id               serial PRIMARY KEY,
    ticker           text NOT NULL,
    dot_date         date NOT NULL,
    cross_depth      numeric,
    drawdown_pct     numeric,
    dd_bucket        text,
    px_at_dot        numeric,
    dist_to_low_pct  numeric,
    fwd_63d_pct      numeric,
    fwd_126d_pct     numeric,
    fwd_252d_pct     numeric,
    era              text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticker, dot_date)
);

CREATE TABLE IF NOT EXISTS greendot15_progress (
    ticker      text PRIMARY KEY,
    n_dots      integer,
    created_at  timestamptz NOT NULL DEFAULT now()
);
