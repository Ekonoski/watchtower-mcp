-- 021: fill auditability (2026-08-27, the 770-fade / 768.655 questions).
--
-- (1) paper_trades.entry_bar — the exact decision bar(s) the entry read,
--     stamped at fill time. The stored spec-bar table keeps FIRST-seen
--     values (ON CONFLICT DO NOTHING) while each spec's decision reads its
--     own fetch — vendor-side settling can make those differ by cents, and
--     today it left two fills the record could not certify. From now on
--     the trade row carries its own evidence.
--
-- (2) fill_audit — one row per forensic question about a recorded fill,
--     answered from finer-grained vendor history (research verification of
--     the record; reconstruction-is-not-tape governs live grading, and
--     this table never touches the books).

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_bar jsonb;

CREATE TABLE IF NOT EXISTS fill_audit (
    id          serial PRIMARY KEY,
    trade_id    integer,
    ticker      text NOT NULL,
    question    text NOT NULL,
    verdict     text NOT NULL,          -- confirmed | refuted | inconclusive*
    detail      text,
    evidence    jsonb,                  -- the 1-minute bars the verdict read
    created_at  timestamptz NOT NULL DEFAULT now()
);
