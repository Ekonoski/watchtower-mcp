-- 028: The daily-dot alignment pass — Eric's correction: his daily
-- trade is the dot PLUS price above the 8/21 (before the EMAs flip),
-- not every raw daily dot. One row per daily dot (all buckets; cohort
-- cuts happen at readout): state at the dot, the clear-both entry
-- inside 15 days (or no_clear), outcomes from the ENTRY.

CREATE TABLE IF NOT EXISTS greendot_ms_align (
    id                  serial PRIMARY KEY,
    ms_id               integer NOT NULL REFERENCES greendot_dots_ms(id)
                        ON DELETE CASCADE,
    above8_at_dot       boolean,
    above21_at_dot      boolean,
    ema_crossed_at_dot  boolean,          -- 8 > 21 already (post-flip)
    cleared             boolean,
    entry_date          date,
    entry_px            numeric,
    premium_pct         numeric,
    mae21_pct           numeric,
    fwd_21d_pct         numeric,
    fwd_63d_pct         numeric,
    fwd_126d_pct        numeric,
    note                text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ms_id)
);
