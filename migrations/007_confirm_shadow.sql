-- 007: the confirmation shadow (2026-08-08).
--
-- Decision (Eric, 2026-08-08): the swing book KEEPS resting-limit fills at
-- the trigger — a clean touch fills at the level, best price. The open
-- question — should every entry demand a completed 15m close back through
-- the trigger, the way reclaims and the gamma book already do — is settled
-- by measurement, not argument: every touch fill records what a
-- confirmation-gated desk would have done with the exact same spec.
--
-- fill_kind: 'touch' (limit filled at the trigger), 'reclaim' (level lost,
--   filled at the first completed 15m close back through), 'close_through'
--   (gamma books — every entry is already a confirmed close).
-- confirm_status:
--   'confirmed'  — confirm_px / confirm_at hold the completed-15m-bar close
--                  a confirmation desk pays for this same trade.
--   'no_confirm' — the entry day ended with no 15m close back through; the
--                  confirmation desk never takes the trade the limit owns.
--   'pending'    — still resolving intraday.
--   'unresolved' — loop gap: the entry day's bars are gone before the shadow
--                  resolved. A data hole — render it as one, never as a zero.
--   'n/a'        — reclaim / gamma entries: the entry itself is the
--                  confirmed close, shadow equals actual by construction.

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS fill_kind text,
  ADD COLUMN IF NOT EXISTS confirm_px numeric,
  ADD COLUMN IF NOT EXISTS confirm_at timestamptz,
  ADD COLUMN IF NOT EXISTS confirm_status text;

ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_confirm_status_chk
  CHECK (confirm_status IN ('pending', 'confirmed', 'no_confirm',
                            'unresolved', 'n/a'));

ALTER TABLE paper_trades ADD CONSTRAINT paper_trades_fill_kind_chk
  CHECK (fill_kind IN ('touch', 'reclaim', 'close_through'));
