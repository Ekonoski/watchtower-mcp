-- 053: the journal keeps the CHARTS (2026-09-04, Eric: "are you also
-- logging the charts that I put in here so that you can use them...
-- later to potentially train yourself off of?" — the answer was no:
-- pasted screenshots live only in a session and the container forgets
-- them). A row now carries links to the chart images the trade or skip
-- was judged on (a Google Drive folder Eric owns), so a later session
-- can open the picture beside the row. The image is the eye's evidence;
-- the machine-readable state lives in the bar-stamped tables. Applied
-- live via MCP.

ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS chart_urls text[];
COMMENT ON COLUMN trade_journal.chart_urls IS 'links to the chart images Eric judged the trade/skip on (Google Drive), so a later session can open the picture beside the row';
