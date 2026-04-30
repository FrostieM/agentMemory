-- Governance metadata for behavior instructions.
--
-- Behavior instructions can shape future agent behavior, so they need a
-- provenance/review lifecycle separate from ordinary retrieved chunks. These
-- columns let quality gates detect unreviewed or expired instructions and let
-- explainability surfaces report why an instruction was suppressed.

ALTER TABLE behavior_instructions ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE behavior_instructions ADD COLUMN source_id TEXT;
ALTER TABLE behavior_instructions ADD COLUMN reviewed_by TEXT;
ALTER TABLE behavior_instructions ADD COLUMN reviewed_at TEXT;
ALTER TABLE behavior_instructions ADD COLUMN expires_at TEXT;
ALTER TABLE behavior_instructions ADD COLUMN last_applied_at TEXT;
ALTER TABLE behavior_instructions ADD COLUMN application_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE behavior_instructions ADD COLUMN conflict_group TEXT;

CREATE INDEX IF NOT EXISTS idx_behavior_instructions_workspace_expires
    ON behavior_instructions(workspace_id, active, expires_at);
CREATE INDEX IF NOT EXISTS idx_behavior_instructions_workspace_conflict
    ON behavior_instructions(workspace_id, conflict_group, priority, updated_at);
