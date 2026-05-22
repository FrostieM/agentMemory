-- Plan-step outcome -> capability maturity (Phase 5c).
--
-- A plan step that reaches a terminal status -- done or skipped -- is
-- an outcome for every capability (skill / role / playbook) bound to it
-- via capability_links. The brain pass feeds that outcome into the
-- capability's success / failure counters through the usage_tracker
-- chokepoint: done -> success, skipped -> failure. (blocked is a
-- transient state, not terminal, so it is not fed.)
--
-- outcome_fed_at is the idempotency marker: NULL until the brain pass
-- has fed the step's outcome, then the ISO timestamp of that pass. A
-- re-running pass skips already-stamped steps, so a capability counter
-- is bumped exactly once per step. Nullable, no default -- every
-- existing row reads as "not yet fed", which is correct: the loop is
-- new, so no historical step has been fed. The first brain pass after
-- this migration therefore feeds every pre-existing terminal step in
-- one legitimate backfill, bounded per pass by
-- MEMORY_PLAN_OUTCOME_MATURITY_MAX_PER_PASS.

ALTER TABLE plan_steps ADD COLUMN outcome_fed_at TEXT;
