-- 038: three studies from the 2026-09-01 live day (rank ladder — the
-- MSFT #2 question; HOD/LOD time map — pass-2 scan P2-19; trail
-- variants — chandelier + efficiency gate on the graded entries).

CREATE TABLE IF NOT EXISTS rankladder_events (
    id          bigserial PRIMARY KEY,
    trade_date  date NOT NULL,
    ticker      text NOT NULL,
    rank_pos    integer NOT NULL,     -- 1..7 by 9:45 RS vs QQQ
    rs_945      numeric NOT NULL,
    qualified   boolean NOT NULL,     -- rs >= +0.4
    entry_ts    timestamptz,          -- NULL = no GO printed
    entry_px    numeric,
    stop_px     numeric,
    orb_state   text,                 -- above / inside / below at entry
    trend5_on   boolean,              -- day-anchored 5m trend gate at entry
    eod_bps     numeric,
    r_close     numeric,
    UNIQUE (trade_date, ticker)
);

CREATE TABLE IF NOT EXISTS hodlod_days (
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    hi_time     text NOT NULL,        -- 15m bucket start, ET 'HH:MM'
    lo_time     text NOT NULL,
    open_state  text NOT NULL,        -- open_above / open_below / inside
    close_pos   numeric,              -- close position in day range 0..1
    PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE IF NOT EXISTS trailvar_events (
    event_id    bigint PRIMARY KEY REFERENCES tapeentry_events(id),
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    variants    jsonb NOT NULL
);
