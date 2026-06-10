-- Write-through persistence for Grok news classification/synthesis caches.
-- In-memory caches die with every deploy; on deploy-heavy days the whole
-- news backlog gets re-billed. Auto-created by news_scanner on first use.
CREATE TABLE IF NOT EXISTS grok_cache (
    key TEXT NOT NULL,
    kind TEXT NOT NULL,            -- 'classification' | 'synthesis'
    payload JSONB NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key, kind)
);
-- Rows older than 24h are pruned opportunistically on each flush.
