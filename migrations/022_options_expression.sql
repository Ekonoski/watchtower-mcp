-- 022: the swing options-expression shadow + fundamentals tag
-- (2026-08-27, Eric: "we also need to be swing trading options...
-- let's definitely build this and get it working asap").
--
-- options_expression: for every swing fill, the option contract the
-- desk WOULD buy (ITM call, DTE by class), priced from the live chain
-- at entry and again at the trade's exit — the wrapper graded against
-- the shares-equivalent per the ledger-grades-the-signal rule. A spec
-- that can't be expressed records WHY (illiquid / no_chain / no_mark)
-- — a gate that filters silently is invisible (_social_block family).
--
-- paper_specs.fundamentals_state: measurement-only tag (osc_state /
-- sector_state pattern) — Piotroski, Altman Z, days-to-earnings —
-- stamped AFTER curation so arming stays blind. Graded on the book's
-- own resolutions; promoted (likely as a veto/warning) only if earned.

CREATE TABLE IF NOT EXISTS options_expression (
    id             serial PRIMARY KEY,
    trade_id       integer NOT NULL UNIQUE,
    ticker         text NOT NULL,
    setup          text,
    verdict        text NOT NULL,   -- ticket | illiquid | no_chain | no_mark | hole
    note           text,
    occ            text,            -- OCC contract symbol
    expiry         date,
    strike         numeric,
    dte            integer,
    delta          numeric,
    iv             numeric,
    iv_rank        numeric,
    oi             integer,
    entry_mark     numeric,         -- chain mark at ticket time
    entry_spot     numeric,         -- share entry px, for the comparison
    exit_mark      numeric,
    exit_spot      numeric,
    earnings       jsonb,           -- report date inside the hold, if any
    priced_at      timestamptz DEFAULT now(),
    exit_priced_at timestamptz
);

ALTER TABLE paper_specs ADD COLUMN IF NOT EXISTS fundamentals_state jsonb;
