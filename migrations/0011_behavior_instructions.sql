-- First-class behavior and instruction memory.
--
-- Procedural rules remain supported, but they are intentionally simple.
-- This table stores persistent agent behavior guidance with explicit kind,
-- scope, priority, and conflict policy so communication style, project
-- conventions, and operating rules can be rendered as instructions rather
-- than buried inside retrieved chunks.

CREATE TABLE IF NOT EXISTS behavior_instructions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'workspace',
    priority TEXT NOT NULL DEFAULT 'user_preference',
    rule TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    applies_to_json TEXT NOT NULL DEFAULT '[]',
    conflict_policy TEXT NOT NULL DEFAULT 'current_user_wins',
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.85,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_behavior_instructions_workspace_active
    ON behavior_instructions(workspace_id, active, priority, updated_at);
CREATE INDEX IF NOT EXISTS idx_behavior_instructions_workspace_kind
    ON behavior_instructions(workspace_id, kind, active);
