-- v3.4 #6 hygiene findings action queue.
--
-- maintenance_events already track WHAT drifted (status: open / resolved
-- / ignored, plus resolved_at). What was missing is the OPERATOR-side
-- workflow: who is looking at this event right now, did they
-- intentionally dismiss it as non-actionable (not the same as
-- auto-resolved by the metric clearing), and what context did they add.
--
-- We add five columns so the queue page can render claim / dismiss /
-- resolve buttons without mutating the existing status semantics.
-- The original `status` keeps tracking the substrate-level state
-- (does the underlying drift still exist?), while `action_status`
-- tracks the operator triage state (is anyone working on this?).
--
--   status              action_status
--   open                open         -- nobody touched it yet
--   open                claimed      -- operator picked it up but didn't finish
--   open                dismissed    -- operator decided it is not actionable
--   resolved            resolved     -- underlying metric cleared OR operator
--                                      marked it done; resolved_at populated
--
-- Idempotent — every column uses ALTER TABLE ADD COLUMN with a default
-- so re-running on a populated DB is safe (SQLite ALTER ADD COLUMN
-- backfills the default in O(1)).

ALTER TABLE maintenance_events ADD COLUMN action_status TEXT NOT NULL DEFAULT 'open';
ALTER TABLE maintenance_events ADD COLUMN assigned_to TEXT;
ALTER TABLE maintenance_events ADD COLUMN action_notes TEXT;
ALTER TABLE maintenance_events ADD COLUMN claimed_at TEXT;
ALTER TABLE maintenance_events ADD COLUMN dismissed_at TEXT;

-- Queue page sorts by created_at DESC filtered by (workspace_id,
-- action_status). Without this index the operator would pay a full
-- table scan once the events table grows past a few thousand rows.
CREATE INDEX IF NOT EXISTS idx_maintenance_events_action_queue
    ON maintenance_events(workspace_id, action_status, created_at DESC);
