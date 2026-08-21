-- 013: The defended-entry shadow + the 15m defense study (2026-08-21).
--
-- Eric: on a retest, instead of taking the touch, wait for a lower-
-- timeframe bounce with volume showing buyers DEFENDING the level —
-- contracting red volume into the touch, a green uptick off it (one or
-- two bars; a relative rise, never a spike requirement — spikes are
-- late). Measurement only, like every convention test on this desk:
-- the live book keeps its blind limits untouched until ~30 resolved
-- comparisons decide (record so far: no-confirm touches -1.17R avg n=4
-- resolved vs confirmed touches -0.36R n=3 — the knives cluster where
-- no defense ever came).
--
-- paper_spec_bars.volume: bars carried OHLC only; the defense signature
-- needs volume. Nullable — pre-2026-08-24 rows are recorded holes and
-- any shadow needing them renders 'unavailable', never a guess.
--
-- paper_defense_shadow: one row per touch-filled trade per variant
-- (v1 = single confirming bar, v2 = two rising-volume bars — both
-- recorded so the data picks the eye's definition, not my guess).
-- shadow_r fills when the LIVE trade exits: same exit price and rules,
-- different entry — the shadow rides the live trade, it never trades.
--
-- defense_study: the historical read. pattern_backtest already records
-- retest_bar per episode; the study fetches the retest day's 15m bars
-- from Polygon (research backtest — the reconstruction-is-not-tape rule
-- governs LIVE grading, not research), applies the same detector, and
-- stores the signature verdict beside the episode's recorded outcome.

ALTER TABLE paper_spec_bars ADD COLUMN IF NOT EXISTS volume numeric;

CREATE TABLE IF NOT EXISTS paper_defense_shadow (
    id          bigserial   PRIMARY KEY,
    trade_id    bigint      NOT NULL,
    variant     text        NOT NULL,   -- 'v1' | 'v2'
    status      text        NOT NULL,   -- pending|defended|knife_skipped|missed|no_defense|unavailable
    defense_px  numeric,
    defense_at  timestamptz,
    base_vol    numeric,                -- avg pullback red-bar volume
    defense_vol numeric,
    premium_pct numeric,                -- (defense_px - trigger)/trigger
    shadow_r    numeric,                -- filled at live exit; 0 for skips
    live_r      numeric,                -- the live trade's R, for the comparison
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trade_id, variant)
);

CREATE TABLE IF NOT EXISTS defense_study (
    episode_id   bigint      NOT NULL,  -- pattern_backtest.id
    variant      text        NOT NULL,
    ticker       text        NOT NULL,
    retest_date  date,
    status       text        NOT NULL,  -- defended|knife|missed|no_defense|unavailable|no_bars
    defense_px   numeric,
    premium_pct  numeric,
    win_1r       boolean,               -- copied from the episode
    realized_r   numeric,               -- copied from the episode
    outcome      text,
    pattern      text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (episode_id, variant)
);

ALTER TABLE paper_defense_shadow ENABLE ROW LEVEL SECURITY;
ALTER TABLE defense_study        ENABLE ROW LEVEL SECURITY;
