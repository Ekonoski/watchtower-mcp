-- 047: the exit-shape study (2026-09-03 — the day trader's exits graded
-- on the desk's own record: take profit at the multi-touch shelves,
-- half off + breakeven, ratcheted runners, momentum-fade exits, time
-- exits; path map; option frame at three deltas). Applied live via MCP.

CREATE TABLE IF NOT EXISTS exit_shape_events (
    source      text NOT NULL,          -- rsl_go / tapeentry
    event_id    bigint NOT NULL,
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    path        jsonb,
    levels      jsonb NOT NULL,
    variants    jsonb NOT NULL,
    PRIMARY KEY (source, event_id)
);
