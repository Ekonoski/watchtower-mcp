-- 075: The day-bias early-touch-RECLAIM study (queued 2026-08-25, ordered
-- 2026-09-18 when Eric ruled day_bias stops counting as a book — 19
-- sessions, 0 fills). One row per cancelled_early day on the stored
-- SPY/QQQ 15m record: did a post-10:30 15m CLOSE back above PDH (a true
-- reclaim, wick rule) grade positively to the close? lost_close marks the
-- cohort where the level was actually LOST on a close before the reclaim;
-- no_reclaim days are recorded as their own state, never dropped.

CREATE TABLE IF NOT EXISTS daybias_reclaim_events (
    id            bigserial PRIMARY KEY,
    ticker        text NOT NULL,
    trade_date    date NOT NULL,
    pdh           numeric NOT NULL,
    state         text NOT NULL,           -- reclaimed / no_reclaim
    touch_ts      timestamptz NOT NULL,    -- the pre-10:30 touch that cancelled the book
    lost_close    boolean,                 -- a 15m close <= PDH before the reclaim bar
    reclaim_ts    timestamptz,
    entry_px      numeric,                 -- the reclaim bar's close
    day_close_px  numeric,
    close_src     text,                    -- daily_prices / last_15m_bar
    eod_bps       numeric,
    mfe_bps       numeric,
    mae_bps       numeric,
    stop_hit      boolean,                 -- 0.75% stop on 15m CLOSES (the book's declared deviation)
    stop_exit_px  numeric,
    stop_bps      numeric,
    stop_r        numeric,                 -- on the 0.75% unit
    bars_after    integer,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticker, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daybias_reclaim_tk_state
    ON daybias_reclaim_events (ticker, state, lost_close);
