-- Phase 3 of the "memory as brain" evolution: consolidation feedback loop.
-- Until now, consolidation.py wrote insights with status='candidate' that
-- sat unused. Phase 3 wires them into:
--
--   (a) brief ## Recent insights section (with throttling)
--   (b) behavior_candidates for clusters that match pinned behavior tokens
--   (c) outcome boost on each evidence episode (so future consolidation
--       runs weight those episodes higher)
--
-- The new ``last_surfaced_at`` column lets the brief throttle re-surfacing:
-- an insight stays in the brief for at most ``surface_window_days`` before
-- the brief composer drops it from the section (still in the table; the
-- operator can still review).
--
-- Companion modules:
--   src/agent_memory_lite/cognition/consolidation.py   (write side)
--   src/agent_memory_lite/cognition/brief.py           (_build_recent_insights)
--   src/agent_memory_lite/compaction/promote_insight_to_behavior.py
--                                                     (promote >= 2 surface events)
--
-- Migration is idempotent. SQLite ALTER TABLE ADD COLUMN tolerates re-runs
-- only via separate IF NOT EXISTS guards, which the schema runner enforces
-- by checking the schema_migrations log -- so this file is single-shot.

ALTER TABLE insights ADD COLUMN last_surfaced_at TEXT;
ALTER TABLE insights ADD COLUMN surface_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_insights_workspace_status_outcome
    ON insights(workspace_id, status, outcome_score DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_insights_surface
    ON insights(workspace_id, surface_count DESC, last_surfaced_at);
