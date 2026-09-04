-- 052: daily_prices sanity sweep (2026-09-04, the SPY 2005-05-27 row —
-- high 1120.2 on a 120 close — found by the NVDA-skip debrief). Every
-- stored daily bar whose open/high/low sits more than 2x from its close
-- is RE-FETCHED from the vendor and replaced with the vendor's current
-- bar; the sweep never hand-edits a price. Verdict per row: corrected
-- (vendor bar differed — the stored row was bad), confirmed (vendor
-- agrees — a real move, typically a warrant or penny name), no_vendor_bar
-- (a hole, stored row left as it was). Applied live via MCP.

CREATE TABLE IF NOT EXISTS price_sanity (
    ticker      text NOT NULL,
    trade_date  date NOT NULL,
    old_open numeric, old_high numeric, old_low numeric, old_close numeric,
    new_open numeric, new_high numeric, new_low numeric, new_close numeric,
    verdict     text NOT NULL CHECK (verdict IN ('corrected', 'confirmed', 'no_vendor_bar')),
    checked_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, trade_date)
);
COMMENT ON TABLE price_sanity IS 'daily_prices rows flagged by the >2x-from-close rule, re-fetched from the vendor; the record of every correction and every confirmation (never a hand edit)';
