-- Initial schema for agent-memory-lite.
--
-- Forward-only migration. The `schema_migrations` tracking table is created by the
-- migration runner before any user migration is applied. PRAGMAs (journal_mode=WAL,
-- foreign_keys=ON, synchronous=NORMAL) are set per connection in `db/pragmas.py`.

CREATE TABLE IF NOT EXISTS workspace_meta (
    workspace_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, key)
);

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT,
    task_id TEXT,
    source_type TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    summary TEXT,
    trust_level TEXT NOT NULL DEFAULT 'unknown',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS core_memory (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source_episode_id TEXT,
    confidence REAL NOT NULL,
    importance REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS task_state (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    current_plan_json TEXT NOT NULL DEFAULT '[]',
    completed_steps_json TEXT NOT NULL DEFAULT '[]',
    next_action TEXT,
    blockers_json TEXT NOT NULL DEFAULT '[]',
    files_in_scope_json TEXT NOT NULL DEFAULT '[]',
    source_episode_id TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, task_id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    decision_text TEXT NOT NULL,
    rationale TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    supersedes_decision_id TEXT,
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.9,
    importance REAL NOT NULL DEFAULT 0.8,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id),
    FOREIGN KEY(supersedes_decision_id) REFERENCES decisions(id)
);

CREATE TABLE IF NOT EXISTS procedural_rules (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'workspace',
    active INTEGER NOT NULL DEFAULT 1,
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.85,
    importance REAL NOT NULL DEFAULT 0.75,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_indexed_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(workspace_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    file_id TEXT,
    episode_id TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    summary TEXT,
    line_start INTEGER,
    line_end INTEGER,
    symbols_json TEXT NOT NULL DEFAULT '[]',
    embedding_id TEXT,
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(file_id) REFERENCES files(id),
    FOREIGN KEY(episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    properties_json TEXT NOT NULL DEFAULT '{}',
    embedding_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, type, canonical_name)
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    subject_entity_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    object_entity_id TEXT,
    literal_value TEXT,
    fact_text TEXT NOT NULL,
    source_episode_id TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.8,
    importance REAL NOT NULL DEFAULT 0.5,
    trust_level TEXT NOT NULL DEFAULT 'unknown',
    observed_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    invalidated_by_fact_id TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(subject_entity_id) REFERENCES entities(id),
    FOREIGN KEY(object_entity_id) REFERENCES entities(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id),
    FOREIGN KEY(invalidated_by_fact_id) REFERENCES facts(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    source_episode_id TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episodes_workspace_created
    ON episodes(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_episodes_workspace_task
    ON episodes(workspace_id, task_id);

CREATE INDEX IF NOT EXISTS idx_chunks_workspace_file
    ON chunks(workspace_id, file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_workspace_episode
    ON chunks(workspace_id, episode_id);

CREATE INDEX IF NOT EXISTS idx_files_workspace_path
    ON files(workspace_id, path);

CREATE INDEX IF NOT EXISTS idx_entities_workspace_name
    ON entities(workspace_id, canonical_name);

CREATE INDEX IF NOT EXISTS idx_facts_workspace_subject
    ON facts(workspace_id, subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_facts_workspace_relation
    ON facts(workspace_id, relation);
CREATE INDEX IF NOT EXISTS idx_facts_active
    ON facts(workspace_id, valid_to, invalidated_by_fact_id);

CREATE INDEX IF NOT EXISTS idx_decisions_active
    ON decisions(workspace_id, status, valid_to);

CREATE INDEX IF NOT EXISTS idx_audit_workspace_created
    ON audit_log(workspace_id, created_at);
