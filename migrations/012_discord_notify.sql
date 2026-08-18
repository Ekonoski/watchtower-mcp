-- 012: Discord notification pipe (2026-08-18).
--
-- Eric: "ok, let's build this and give it a shot." The gamma board moves
-- intraday (proven on the recorded gex_intraday day-paths — the CPI-day
-- 775->780 wall walk, today's QQQ flip walking 724.72->723.21) and
-- TradingView cannot ingest data (Pine sandbox), so the update loop is
-- Watchtower -> Discord -> Eric's slot inputs. Two streams to start:
-- #gamma-drift (material board re-marks, formatted as Tape Bot slot
-- values) and #desk (the paper desk narrating fills/exits in real time).
--
-- discord_notify_log: at-most-once delivery claims. Two scheduler
-- containers both poll; the (kind, ref) PK is the claim, same family as
-- scheduler_job_claims. delivered=false rows are visible failures, never
-- silent (a lost alert is a data hole and the table shows it).
--
-- gamma_drift_state: the marks Eric currently holds (seeded from the
-- morning board at 9:20 ET, BEFORE the 9:35 intraday upsert overwrites
-- gex_levels; advanced to whatever each alert sent). Drift is measured
-- against what's in his slots, not against an abstract baseline.
--
-- gamma_drift_alerts: every material-change evaluation, sent or
-- suppressed, with the reason. The log is the record of how much the
-- board actually walks intraday — it decides later whether morning marks
-- suffice on pinning days (measured, not argued).

CREATE TABLE IF NOT EXISTS discord_notify_log (
    kind       text        NOT NULL,
    ref        text        NOT NULL,
    channel    text        NOT NULL,
    delivered  boolean,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at    timestamptz,
    PRIMARY KEY (kind, ref)
);

CREATE TABLE IF NOT EXISTS gamma_drift_state (
    ticker        text        NOT NULL,
    trade_date    date        NOT NULL,
    call_wall     numeric,
    put_wall      numeric,
    gamma_flip    numeric,
    regime        text,
    net_gex       numeric,
    source        text        NOT NULL,  -- 'morning_board' | 'first_seen' | 'alert'
    last_alert_at timestamptz,
    alerts_sent   int         NOT NULL DEFAULT 0,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, trade_date)
);

CREATE TABLE IF NOT EXISTS gamma_drift_alerts (
    id                bigserial   PRIMARY KEY,
    ticker            text        NOT NULL,
    trade_date        date        NOT NULL,
    snapshot_ts       timestamptz NOT NULL,
    changes           jsonb       NOT NULL,
    alerted           boolean     NOT NULL,
    suppressed_reason text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS gamma_drift_alerts_day
    ON gamma_drift_alerts (trade_date, ticker);

-- Same posture as 008/010: all access goes through the direct Postgres
-- connection (table owner, unbound by RLS); the anon/authenticated
-- surface stays closed.
ALTER TABLE discord_notify_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE gamma_drift_state  ENABLE ROW LEVEL SECURITY;
ALTER TABLE gamma_drift_alerts ENABLE ROW LEVEL SECURITY;
