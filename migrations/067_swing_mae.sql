-- 067: the swing book's MAE-vs-stop read (2026-09-10 — Eric: "run the
-- MAE read tonight"). One row per swing fill, upserted each pass:
-- excursion in R from the trade's own recorded 15m bars, the stop's
-- shape (% and ATR14), the post-exit 20-day path for stopped trades
-- (shakeout / failure / pending — pending is a hole, never a failure),
-- and the declared counterfactual stops replayed through the book's own
-- exit rule (cf JSONB; a NULL variant is an ATR hole). Applied live via
-- MCP.

CREATE TABLE IF NOT EXISTS swing_mae_events (
    trade_id            bigint PRIMARY KEY,
    spec_id             bigint NOT NULL,
    ticker              text NOT NULL,
    setup               text NOT NULL,
    entered_at          timestamptz NOT NULL,
    exited_at           timestamptz,
    exit_reason         text,
    live_r              numeric,
    entry_px            numeric NOT NULL,
    stop_px             numeric NOT NULL,
    target_px           numeric NOT NULL,
    stop_pct            numeric,
    atr14               numeric,
    stop_atr            numeric,
    n_bars              integer NOT NULL,
    mae_touch_r         numeric,
    mae_close_r         numeric,
    mae_daily_r         numeric,
    mfe_r               numeric,
    mfe_pre_mae_r       numeric,
    t_mae_days          smallint,
    t_exit_days         smallint,
    touched_stop_first  boolean,
    post_days           smallint,
    post_max_r          numeric,
    post_min_r          numeric,
    reclaim_day         smallint,
    target_touched      boolean,
    verdict             text NOT NULL,
    live_match          boolean,
    cf                  jsonb NOT NULL DEFAULT '{}'::jsonb,
    graded_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS swing_mae_events_verdict ON swing_mae_events (verdict);
