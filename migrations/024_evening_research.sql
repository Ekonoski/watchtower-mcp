-- 024: the Friday-evening research batch (2026-08-28).
--
-- gamma_mega_replay — the index gamma playbook graded on the mega-cap
--   boards (Eric: "will the system also trade those?" → replay first,
--   arm only if it survives). Same build_gamma_specs + simulate_day
--   code path as the Tier-1 harness; bars are research-fetched 15m
--   history (reconstruction-is-not-tape governs LIVE grading only).
--
-- gamma_target_shadow — frozen target vs walking target, per resolved
--   gamma trade, replayed from RECORDED bars + RECORDED 15-min boards.
--   Two variants: walk_both, walk_toward. Stops never walk.
--
-- greendot_dots — the 16D below-zero green-dot study (Eric: "the GOAT
--   of mid-to-long-term holds"). One row per dot, fleet-wide, fixed-
--   anchor 16-day bars (end-anchored resamples repaint — the BW-3D
--   lesson), with drawdown bucket, forward outcomes, and cohort
--   baselines computed beside them. VFF is the named archetype.

CREATE TABLE IF NOT EXISTS gamma_mega_replay (
    id         serial PRIMARY KEY,
    trade_date date NOT NULL,
    ticker     text NOT NULL,
    setup      text NOT NULL,
    direction  text NOT NULL,
    entry_px   numeric, exit_px numeric,
    entered_et timestamptz, exited_et timestamptz,
    reason     text,
    r          numeric,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gamma_target_shadow (
    id         serial PRIMARY KEY,
    trade_id   integer NOT NULL,
    variant    text NOT NULL,          -- walk_both | walk_toward
    exit_px    numeric, exit_reason text, r numeric,
    live_r     numeric,
    n_board_moves integer,             -- how often the wall re-priced mid-hold
    note       text,
    created_at timestamptz DEFAULT now(),
    UNIQUE (trade_id, variant)
);

CREATE TABLE IF NOT EXISTS greendot_progress (
    ticker text PRIMARY KEY,
    n_dots integer NOT NULL DEFAULT 0,
    processed_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS greendot_dots (
    id            serial PRIMARY KEY,
    ticker        text NOT NULL,
    dot_date      date NOT NULL,       -- the 16D bar's END date
    cross_depth   numeric,             -- wt value at the cross (below 0 by def)
    drawdown_pct  numeric,             -- close vs trailing 2y high at the dot
    dd_bucket     text,                -- lt30 | b30_50 | b50_70 | gte70
    px_at_dot     numeric,
    fwd_low_6m    numeric,             -- lowest close in the next 126 trading days
    dist_to_low_pct numeric,           -- how far below the dot that low sat
    lower_dot_followed boolean,        -- did a later dot print at a lower price?
    fwd_63d_pct   numeric, fwd_126d_pct numeric, fwd_252d_pct numeric,
    era           text,                -- pre2016 | post2016
    UNIQUE (ticker, dot_date)
);
