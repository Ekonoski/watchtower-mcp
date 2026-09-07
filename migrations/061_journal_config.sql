-- 061: journal configuration for the human scoreboard (2026-09-07, Eric:
-- "my goal as a human trader will be to beat the S&P 500"). The journal
-- grades Eric's account against SPY total return under the Beat-SPY rules
-- (higher compound return AND no deeper drawdown). starting_equity is
-- ASSUMED until set — the render says so. Applied live via MCP.

CREATE TABLE IF NOT EXISTS journal_config (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    note       text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO journal_config (key, value, note) VALUES
    ('starting_equity', '25000', 'ASSUMED ($250 R at 1% risk) — set the real figure with watchtower_journal_config'),
    ('scoreboard_start', '2026-09-01', 'first day of the manual book on the $250 R rule')
ON CONFLICT (key) DO NOTHING;
