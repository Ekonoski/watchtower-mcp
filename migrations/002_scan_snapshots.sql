-- Scan snapshots: every scheduled/manual scan is persisted here so the
-- dashboard can show the latest market picture without re-running the scan.
CREATE TABLE IF NOT EXISTS scan_snapshots (
    id BIGSERIAL PRIMARY KEY,
    scan_type TEXT NOT NULL DEFAULT 'intraday',
    as_of TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_market_hours BOOLEAN,
    minutes_elapsed INT,
    signal_count INT NOT NULL DEFAULT 0,
    signals JSONB NOT NULL DEFAULT '[]'::jsonb,
    news JSONB NOT NULL DEFAULT '[]'::jsonb,
    market_pulse JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_scan_snapshots_as_of
    ON scan_snapshots (scan_type, as_of DESC);

-- Keep the table from growing unbounded: snapshots older than 14 days carry
-- no value (alert_log holds the long-term signal history).
-- Run periodically or rely on dashboard.store's opportunistic cleanup:
--   DELETE FROM scan_snapshots WHERE as_of < now() - interval '14 days';
