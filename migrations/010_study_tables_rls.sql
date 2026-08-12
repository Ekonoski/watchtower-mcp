-- 010: RLS on the study tables (2026-08-12).
--
-- The goat_* and cipher_* study tables were created ad hoc by their studies
-- (2026-08-10/11) outside the migration flow, and shipped with row level
-- security disabled — fully readable AND writable to anyone holding the
-- project's anon key. Backtest evidence that gates real book decisions
-- (the wma_touch prior, the cipher tag thresholds) should not be mutable
-- from the public surface.
--
-- Every reader and writer of these tables in this codebase goes through the
-- direct Postgres connection (screen.reversal_screen._conn — table owner,
-- which RLS does not bind), so enabling RLS with no policies closes the
-- anon/authenticated surface without touching a single job. Same posture as
-- every migration-managed table here (see 008).

ALTER TABLE goat_events          ENABLE ROW LEVEL SECURITY;
ALTER TABLE goat_done            ENABLE ROW LEVEL SECURITY;
ALTER TABLE goat_universe        ENABLE ROW LEVEL SECURITY;
ALTER TABLE goat_spy             ENABLE ROW LEVEL SECURITY;
ALTER TABLE goat_wk_done         ENABLE ROW LEVEL SECURITY;
ALTER TABLE goat_et_events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE goat_et_done         ENABLE ROW LEVEL SECURITY;
ALTER TABLE goat_osc             ENABLE ROW LEVEL SECURITY;
ALTER TABLE goat_osc_done        ENABLE ROW LEVEL SECURITY;
ALTER TABLE cipher_episode_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE cipher_study_done    ENABLE ROW LEVEL SECURITY;
