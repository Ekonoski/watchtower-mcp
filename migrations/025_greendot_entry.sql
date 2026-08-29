-- 025: The green-dot ENTRY-SCHEDULE study (2026-08-29).
-- One row per (dot, variant): dot_lump / ladder / raw_green /
-- ha_doji_any / ha_doji_brk. A variant that never fires records
-- entered=false with the reason in note (missed_runner / still_falling
-- / ticker_error hole rows) — a silent filter is a _social_block.
-- UNIQUE(dot_id, variant) backs the module's ON CONFLICT DO NOTHING.

CREATE TABLE IF NOT EXISTS greendot_entry (
    id            bigserial PRIMARY KEY,
    dot_id        integer NOT NULL REFERENCES greendot_dots(id)
                  ON DELETE CASCADE,
    variant       text NOT NULL,
    entered       boolean NOT NULL,
    entry_date    date,
    entry_px      numeric,
    deployed_frac numeric,
    mae_pct       numeric,
    fwd6m_pct     numeric,
    fwd12m_pct    numeric,
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dot_id, variant)
);

CREATE INDEX IF NOT EXISTS idx_greendot_entry_variant
    ON greendot_entry (variant, entered);
