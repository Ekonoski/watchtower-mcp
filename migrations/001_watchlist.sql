CREATE TABLE IF NOT EXISTS watchlist (
    ticker TEXT PRIMARY KEY,
    notes TEXT,
    active BOOLEAN DEFAULT true,
    added_at TIMESTAMPTZ DEFAULT now()
);

-- Seed with a few example tickers
INSERT INTO watchlist (ticker, notes) VALUES
    ('ONDS', 'Defense tech — intraday momentum watch'),
    ('SPY', 'Regime reference')
ON CONFLICT (ticker) DO NOTHING;
