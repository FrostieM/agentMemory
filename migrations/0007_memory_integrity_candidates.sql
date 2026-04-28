-- Memory integrity hardening.
--
-- memory_candidates keeps extractor output reviewable instead of immediately
-- mutating active decisions/rules/core memory. maintenance_events captures
-- retrieval-index failures that can happen after the primary SQLite write has
-- already committed.

CREATE TABLE IF NOT EXISTS memory_candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT,
    evidence TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    importance REAL NOT NULL DEFAULT 0.5,
    trust_level TEXT NOT NULL DEFAULT 'unknown',
    temporal_json TEXT NOT NULL DEFAULT '{}',
    write_targets_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    source_episode_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    promoted_target_type TEXT,
    promoted_target_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_workspace_status
    ON memory_candidates(workspace_id, status, importance, updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_source
    ON memory_candidates(workspace_id, source_episode_id);

CREATE TABLE IF NOT EXISTS maintenance_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    status TEXT NOT NULL DEFAULT 'open',
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    source_episode_id TEXT,
    target_type TEXT,
    target_id TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_maintenance_events_workspace_status
    ON maintenance_events(workspace_id, status, severity, created_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_target
    ON maintenance_events(workspace_id, target_type, target_id);
