-- 062: the green-dot RECOUNT (2026-09-07). Eric ruled Compass = Market
-- Cipher B on every bar he checked, so the eye's waves are 9/12/3 while
-- the engine's are LazyBear's 10/21/4. The recount recomputes the 16D
-- dots under 9/12/3 on the SAME fixed-anchor blocks, tags Cipher's
-- BOTTOM BUY dot (cross-up with the light wave below -60) beside the
-- below-zero definition, and stamps series defects (splice / hole, from
-- beat_spy.series_defects) on both records so the deep cohort can be
-- re-graded clean. Writes greendot_dots_cb + progress; adds four columns
-- to greendot_dots (defect flags, light wave at the cross, bottom-buy).
-- Applied live via MCP.

CREATE TABLE IF NOT EXISTS greendot_dots_cb (
    ticker           text NOT NULL,
    dot_date         date NOT NULL,
    wt1_at_cross     numeric,
    wt2_at_cross     numeric,
    below_zero       boolean NOT NULL,
    bottom_buy       boolean NOT NULL,
    cross_depth      numeric,
    drawdown_pct     numeric,
    dd_bucket        text,
    px_at_dot        numeric,
    dist_to_low_pct  numeric,
    fwd_63d_pct      numeric,
    fwd_126d_pct     numeric,
    fwd_252d_pct     numeric,
    defect_prior     boolean NOT NULL DEFAULT false,
    defect_fwd       boolean NOT NULL DEFAULT false,
    era              text,
    PRIMARY KEY (ticker, dot_date)
);
CREATE INDEX IF NOT EXISTS idx_greendot_cb_cohort ON greendot_dots_cb (below_zero, drawdown_pct, cross_depth);

CREATE TABLE IF NOT EXISTS greendot_recount_progress (
    ticker      text PRIMARY KEY,
    n_dots_cb   integer NOT NULL DEFAULT 0,
    n_dots_lb   integer NOT NULL DEFAULT 0,
    n_defects   integer NOT NULL DEFAULT 0,
    done_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE greendot_dots ADD COLUMN IF NOT EXISTS defect_prior boolean;
ALTER TABLE greendot_dots ADD COLUMN IF NOT EXISTS defect_fwd boolean;
ALTER TABLE greendot_dots ADD COLUMN IF NOT EXISTS wt1_at_cross numeric;
ALTER TABLE greendot_dots ADD COLUMN IF NOT EXISTS bottom_buy boolean;
