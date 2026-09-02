-- 039: strike x expiry dealer gamma (2026-09-02, the SPXVIX-card
-- comparison — "where the strongest walls actually are"). The board
-- tables keep the aggregate walls/flip; this grid keeps WHICH EXPIRY
-- holds the weight, bounded (strikes within GRID_PCT of spot, expiries
-- within GRID_DTE, cells >= GRID_MIN_BN) so a snapshot is a few hundred
-- rows, not tens of thousands. Absent cell = below the floor, never
-- "zero gamma". Applied live via MCP the same evening.

CREATE TABLE IF NOT EXISTS gex_strike_expiry (
    ticker     text NOT NULL,
    ts         timestamptz NOT NULL,
    expiry     date NOT NULL,
    strike     numeric NOT NULL,
    gex_bn     numeric NOT NULL,        -- net dealer gamma $bn (calls +, puts -)
    call_bn    numeric NOT NULL,
    put_bn     numeric NOT NULL,
    PRIMARY KEY (ticker, ts, expiry, strike)
);
CREATE INDEX IF NOT EXISTS gex_strike_expiry_tk_ts ON gex_strike_expiry (ticker, ts DESC);

-- 040 (same evening): wall STRENGTH on the board rows — weight, share of
-- the side's gamma, next-strongest strike — so the 🌅 board and the 📍
-- prox alert can say how load-bearing a wall is, not just where it is.
ALTER TABLE gex_levels ADD COLUMN IF NOT EXISTS wall_strength jsonb;
ALTER TABLE gex_intraday ADD COLUMN IF NOT EXISTS wall_strength jsonb;
