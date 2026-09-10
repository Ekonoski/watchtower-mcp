-- 066: the range-edge failure test (2026-09-10 — Eric: "Run the failure
-- test study now"). A probe beyond PDH/PDL/ORBH/ORBL on a 15m close whose
-- next bar closes back inside; entry against the break, stop at the
-- probe's extreme (close rule and touch rule both recorded), target the
-- opposite edge on touch, eod otherwise. failtest_days records every
-- graded day (zero events is data; a hole has n_events NULL). Applied
-- live via MCP.

CREATE TABLE IF NOT EXISTS failtest_events (
    id            bigserial PRIMARY KEY,
    ticker        text NOT NULL,
    trade_date    date NOT NULL,
    family        text NOT NULL,
    side          text NOT NULL,
    direction     text NOT NULL,
    level_px      numeric NOT NULL,
    target_px     numeric NOT NULL,
    probe_ts      timestamptz NOT NULL,
    entry_ts      timestamptz NOT NULL,
    entry_px      numeric NOT NULL,
    stop_px       numeric NOT NULL,
    rr_declared   numeric,
    outcome_close text NOT NULL,
    r_close       numeric NOT NULL,
    bps_close     numeric NOT NULL,
    bars_close    smallint NOT NULL,
    outcome_touch text NOT NULL,
    r_touch       numeric NOT NULL,
    bps_touch     numeric NOT NULL,
    mfe_bps       numeric,
    mae_bps       numeric,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticker, trade_date, family)
);
CREATE INDEX IF NOT EXISTS failtest_events_tk_d ON failtest_events (ticker, trade_date);

CREATE TABLE IF NOT EXISTS failtest_days (
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    n_events    smallint,
    n_bars      smallint,
    PRIMARY KEY (ticker, trade_date)
);
