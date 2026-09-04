-- 056: vendor anomalies (2026-09-04, Eric: "yes, flag those five rows as
-- vendor anomalies"). The price-sanity sweep re-fetched every suspect
-- daily bar and the vendor CONFIRMED five decimal-shift prints on the
-- liquid set — Polygon itself carries them. The stored values are NOT
-- edited (never hand-edit a price); the rows are flagged with a new
-- verdict, and `daily_prices_clean` nulls the open/high/low of a flagged
-- row so readers that fall back to the close (the oscillator's daily
-- fetch already does) read the day as its close. Closes were correct on
-- all five. Applied live via MCP.

ALTER TABLE price_sanity DROP CONSTRAINT IF EXISTS price_sanity_verdict_check;
ALTER TABLE price_sanity ADD CONSTRAINT price_sanity_verdict_check
    CHECK (verdict IN ('corrected', 'confirmed', 'no_vendor_bar', 'vendor_anomaly'));

UPDATE price_sanity SET verdict = 'vendor_anomaly', checked_at = now()
WHERE (ticker, trade_date) IN (('SPY', DATE '2005-05-27'), ('SPY', DATE '2006-01-26'),
                               ('SPY', DATE '2008-09-29'), ('IWM', DATE '2008-09-19'),
                               ('IWM', DATE '2009-06-16'));

CREATE OR REPLACE VIEW daily_prices_clean AS
SELECT d.ticker, d.trade_date,
       CASE WHEN s.verdict = 'vendor_anomaly' THEN NULL ELSE d.open END AS open,
       CASE WHEN s.verdict = 'vendor_anomaly' THEN NULL ELSE d.high END AS high,
       CASE WHEN s.verdict = 'vendor_anomaly' THEN NULL ELSE d.low  END AS low,
       d.close, d.volume, d.created_at,
       COALESCE(s.verdict = 'vendor_anomaly', false) AS vendor_anomaly
FROM daily_prices d
LEFT JOIN price_sanity s ON s.ticker = d.ticker AND s.trade_date = d.trade_date;
COMMENT ON VIEW daily_prices_clean IS 'daily_prices with vendor-confirmed impossible prints (price_sanity.verdict = vendor_anomaly) nulled on open/high/low; the raw table is never edited';
