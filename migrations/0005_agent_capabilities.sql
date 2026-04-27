-- Agent capability memory layer.
--
-- Roles, skills, and playbooks are operational memory objects. They describe
-- who should handle a class of work, what reusable skill is available, and
-- which repeatable workflow should be followed. They complement archival
-- episodes and research objects by making execution knowledge retrievable.

CREATE TABLE IF NOT EXISTS agent_roles (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    responsibilities_json TEXT NOT NULL DEFAULT '[]',
    boundaries_json TEXT NOT NULL DEFAULT '[]',
    handoff_triggers_json TEXT NOT NULL DEFAULT '[]',
    tools_json TEXT NOT NULL DEFAULT '[]',
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS agent_skills (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    when_to_use_json TEXT NOT NULL DEFAULT '[]',
    inputs_json TEXT NOT NULL DEFAULT '[]',
    outputs_json TEXT NOT NULL DEFAULT '[]',
    tools_json TEXT NOT NULL DEFAULT '[]',
    related_roles_json TEXT NOT NULL DEFAULT '[]',
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS agent_playbooks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    triggers_json TEXT NOT NULL DEFAULT '[]',
    steps_json TEXT NOT NULL DEFAULT '[]',
    success_criteria_json TEXT NOT NULL DEFAULT '[]',
    required_skills_json TEXT NOT NULL DEFAULT '[]',
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_roles_workspace_name
    ON agent_roles(workspace_id, name, active);
CREATE INDEX IF NOT EXISTS idx_agent_skills_workspace_name
    ON agent_skills(workspace_id, name, active);
CREATE INDEX IF NOT EXISTS idx_agent_playbooks_workspace_name
    ON agent_playbooks(workspace_id, name, active);
