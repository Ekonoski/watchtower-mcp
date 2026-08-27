-- 023: the cipher exemplar museum (2026-08-27, Eric: "we just haven't
-- been able to master those on a mechanical level yet" → "build the
-- MCP tool"). Every chart Eric's eye rules on — take or pass — becomes
-- a labeled exemplar: the FULL oscillator state at that moment, from
-- the system's own stored scan (never a screenshot). The mechanical
-- cipher-entry definition gets derived from this set, per the standing
-- BW-3D rule: chart-look composites wait for labeled exemplars; the
-- eye is not specified by adjectives.

CREATE TABLE IF NOT EXISTS cipher_exemplars (
    id          serial PRIMARY KEY,
    ticker      text NOT NULL,
    timeframe   text NOT NULL,           -- 1h | 4h | daily | weekly
    label       text NOT NULL,           -- take | pass
    note        text,                    -- Eric's words, verbatim
    source      text NOT NULL DEFAULT 'live',   -- live | retro
    state       text NOT NULL,           -- captured | hole
    osc         jsonb,                   -- full oscillator_scan row copy
    bar_ts      text,                    -- the state's own freshness stamp
    price       numeric,
    price_asof  date,
    logged_at   timestamptz NOT NULL DEFAULT now()
);
