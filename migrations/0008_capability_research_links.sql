-- Capability links for research discipline.
--
-- Roles, skills, and playbooks should influence hypotheses and experiments,
-- not merely appear as a passive context block. This table links capability
-- objects to theories, evidence, experiments, insights, candidates, or
-- decisions with an explicit relation and rationale.

CREATE TABLE IF NOT EXISTS capability_links (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    capability_type TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    relation TEXT NOT NULL,
    rationale TEXT,
    strength REAL NOT NULL DEFAULT 0.7,
    source_episode_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(
        workspace_id,
        target_type,
        target_id,
        capability_type,
        capability_id,
        relation
    ),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_capability_links_target
    ON capability_links(workspace_id, target_type, target_id, strength);
CREATE INDEX IF NOT EXISTS idx_capability_links_capability
    ON capability_links(workspace_id, capability_type, capability_id, strength);
