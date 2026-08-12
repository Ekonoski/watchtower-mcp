-- 009: the binary-day shadow (2026-08-12).
--
-- The CPI post-mortem (Eric, 2026-08-12): the binary gate skipped all four
-- gamma specs for the WHOLE day, but the print resolved by mid-morning —
-- every recorded gex_intraday snapshot from 9:35 on read pinning, and none
-- of the four triggers ever printed. The skip cost exactly 0R that day; the
-- desk only knew it from 15-minute gex snapshots, because the bar watcher
-- never subscribes to skipped specs and paper_spec_bars held no SPY/QQQ.
--
-- Whether the full-day skip over-pays for its protection is measured, not
-- argued — the confirmation-shadow pattern. Every skipped_binary spec
-- shadow-re-arms at 10:30 ET IF the recorded 10:30 board still shows its
-- level, then grades by the live gamma-book rules from recorded bars. The
-- shadow never places a trade, never touches paper_specs.status, and the
-- skipped tickers' bars now persist so the counterfactual replays from tape.
--
-- rearmed:
--   true  — level held at 10:30; the shadow trades it on paper's paper.
--   false — the board moved off the level (wall/flip moved, regime turned);
--           the morning's trade no longer existed to re-arm.
--   NULL  — no fresh gex_intraday board at 10:30. A data hole, rendered as
--           one — never as "the shadow said no".
-- entered_at NULL with rearmed=true: the trigger never filled after 10:30 —
--   "the skip cost 0R" stated from tape, the most common expected row.
-- exited_at NULL with entered_at set: the day's bar record ended mid-trade
--   (deploy gap, crash) — unresolved, a hole, never graded as a flat close.
--
-- Promotion gate, same as every shadow: ~30 shadow-resolved binary-day
-- specs, then mid-morning re-arming goes live or the full-day skip stands
-- vindicated in the record.

CREATE TABLE IF NOT EXISTS paper_shadow_rearm (
    spec_id     bigint PRIMARY KEY REFERENCES paper_specs(id),
    decided_at  timestamptz NOT NULL,   -- the 10:30 ET decision anchor
    rearmed     boolean,                -- NULL = board unavailable (hole)
    reason      text NOT NULL,          -- always named, both directions
    entered_at  timestamptz,
    entry_px    numeric,
    exited_at   timestamptz,
    exit_px     numeric,
    exit_reason text,                   -- stop | target | eod_flat
    r_multiple  numeric,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE paper_shadow_rearm ENABLE ROW LEVEL SECURITY;
