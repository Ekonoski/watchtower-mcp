-- 011: FVG zones join the record (2026-08-13).
--
-- The gamma board's Imbalances section could only render when the live
-- engine was reachable: detect_fvgs was computed per-request (dashboard
-- drawer) and never persisted, so any session without the service — the
-- one building the Aug 13 board, for instance — shipped a hole where the
-- zones belonged. Same family as every other board input that got fixed
-- by persistence: gex_levels, pattern_scan, paper_spec_bars. Computed
-- fresh each morning from OUR recorded daily bars, written here, read by
-- anyone.
--
-- Two tables so that absence is never ambiguous (the _social_block
-- lesson): a RUN row per ticker per sweep says "we looked, through this
-- bar, at this many bars" — n_zones = 0 is a recorded quiet day, a
-- missing run row is a hole. Zones carry their formation date per row
-- (a zone without its date sends the reader hunting the whole chart).
--
-- Canonical read (freshest run per ticker + its zones):
--   SELECT r.ticker, r.bars_through, r.n_zones, z.side, z.status,
--          z.top, z.bottom, z.mid, z.age_bars, z.formed, z.inverted_on
--   FROM (SELECT DISTINCT ON (ticker) * FROM fvg_runs
--         ORDER BY ticker, computed_at DESC) r
--   LEFT JOIN fvg_zones z ON z.run_id = r.id
--   ORDER BY r.ticker, z.age_bars;

CREATE TABLE IF NOT EXISTS fvg_runs (
    id          bigserial PRIMARY KEY,
    ticker      text NOT NULL,
    timeframe   text NOT NULL DEFAULT 'daily',
    bars_through date NOT NULL,     -- last completed bar the detection saw
    n_bars      int  NOT NULL,      -- how much record it stood on
    n_zones     int  NOT NULL,      -- 0 is data; a missing row is a hole
    computed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fvg_runs_tk_idx ON fvg_runs (ticker, computed_at DESC);

CREATE TABLE IF NOT EXISTS fvg_zones (
    run_id      bigint NOT NULL REFERENCES fvg_runs(id),
    side        text NOT NULL,      -- bullish | bearish
    status      text NOT NULL,      -- open | inverted (filled zones never persist)
    top         numeric NOT NULL,
    bottom      numeric NOT NULL,
    mid         numeric NOT NULL,
    age_bars    int NOT NULL,
    formed      date,               -- the displacement candle's session
    inverted_on date
);

CREATE INDEX IF NOT EXISTS fvg_zones_run_idx ON fvg_zones (run_id);

ALTER TABLE fvg_runs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE fvg_zones ENABLE ROW LEVEL SECURITY;
