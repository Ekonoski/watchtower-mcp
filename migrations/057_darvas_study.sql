-- 057: the Darvas Box study (2026-09-05, Eric: "back test the Nicolas
-- Darvas box theory and tell me its win results and profit"). One row
-- per (ticker, entry, variant): the three exit variants share the same
-- entries; random_close is the same-geometry control. r is stored RAW
-- and capped at readout (±10R, the outlier lesson). A trade the record
-- cuts mid-hold carries exit_reason='record_end' with NULL outcomes — a
-- hole, never a zero. Spec in analysis/darvas_study.py. Applied live.

CREATE TABLE IF NOT EXISTS darvas_events (
    ticker          text NOT NULL,
    entry_date      date NOT NULL,
    variant         text NOT NULL,
    entry_px        numeric NOT NULL,
    box_top         numeric,
    box_bottom      numeric NOT NULL,
    box_height_pct  numeric,
    vol_confirm     boolean,
    spy_above_200   boolean,
    at_ath          boolean,
    era             text NOT NULL,
    dollar_vol      numeric,
    exit_date       date,
    exit_px         numeric,
    exit_reason     text NOT NULL,
    hold_days       integer,
    r               numeric,
    pct             numeric,
    mfe_r           numeric,
    mae_r           numeric,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, entry_date, variant)
);
CREATE INDEX IF NOT EXISTS idx_darvas_events_variant_era ON darvas_events (variant, era);
CREATE INDEX IF NOT EXISTS idx_darvas_events_entry ON darvas_events (entry_date);

CREATE TABLE IF NOT EXISTS darvas_progress (
    ticker      text PRIMARY KEY,
    n_events    integer NOT NULL DEFAULT 0,
    done_at     timestamptz NOT NULL DEFAULT now()
);
