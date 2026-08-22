-- 014: The retro defense read (2026-08-22).
--
-- Eric asked whether the desk's OWN past touch fills can be graded
-- against the defense signature. As research, yes — same footing as
-- defense_study: Polygon 15m history + the live find_defense detector.
-- Its verdicts get their own table so the retro read can never
-- masquerade as the live shadow record (paper_defense_shadow stays
-- Monday-forward, measured on recorded tape only).

CREATE TABLE IF NOT EXISTS defense_retro (
    trade_id     bigint      NOT NULL,   -- paper_trades.id
    variant      text        NOT NULL,   -- 'v1' | 'v2'
    ticker       text        NOT NULL,
    setup        text,
    entry_date   date,
    status       text        NOT NULL,   -- defended|knife_skipped|missed|no_defense|unavailable|no_bars|no_touch
    defense_px   numeric,
    premium_pct  numeric,
    live_r       numeric,                -- NULL while the live trade is open
    shadow_r     numeric,                -- re-priced at the live exit; 0 for skips
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_id, variant)
);

ALTER TABLE defense_retro ENABLE ROW LEVEL SECURITY;
