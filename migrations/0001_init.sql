-- agent-memory-lite v1.0.0 — consolidated initial schema.
--
-- This file replaces the historical 0001_init through 0019_*
-- migration chain. Cold-start now applies one migration to land
-- the full v1.0.0 schema instead of replaying 19 incremental
-- ALTER TABLE / CREATE TABLE / CREATE INDEX statements.
--
-- For new databases this is the only migration needed. The
-- migration runner (db/migrations.py) tracks it as applied in
-- schema_migrations, so subsequent forward-only migrations
-- (0002_*, 0003_*, ...) chain in normally over time.
--
-- Tables: episodes, chunks, files, decisions, theories,
-- research_experiments, research_results, memory_snapshots,
-- research_insights, domain_concepts, agent_roles, agent_skills,
-- agent_playbooks, capability_links, behavior_instructions,
-- core_memory, task_state, procedural_rules, entities, facts,
-- audit_log, memory_candidates, maintenance_events,
-- workspace_manifest, vector_index_metadata,
-- memory_usage_feedback, memory_state_snapshots, workspace_meta,
-- chunks_fts (FTS5 virtual table).
--
-- FTS5 is synced application-side (see fts/chunks_fts.py); no
-- triggers are installed here.

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    workspace_id UNINDEXED,
    path,
    symbols,
    text,
    summary,
    tokenize = 'unicode61'
);

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
, label TEXT, is_archived INTEGER NOT NULL DEFAULT 0);

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
    updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
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
    updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
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
    metadata_json TEXT NOT NULL DEFAULT '{}', is_archived INTEGER NOT NULL DEFAULT 0,
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
    metadata_json TEXT NOT NULL DEFAULT '{}', label TEXT, is_archived INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS 'chunks_fts_data'(id INTEGER PRIMARY KEY, block BLOB);

CREATE TABLE IF NOT EXISTS 'chunks_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS 'chunks_fts_content'(id INTEGER PRIMARY KEY, c0, c1, c2, c3, c4, c5);

CREATE TABLE IF NOT EXISTS 'chunks_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);

CREATE TABLE IF NOT EXISTS 'chunks_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS theories (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'general',
    claim TEXT NOT NULL,
    mechanism TEXT,
    predictions_json TEXT NOT NULL DEFAULT '[]',
    experiment_plan TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'proposed',
    supersedes_theory_id TEXT,
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    importance REAL NOT NULL DEFAULT 0.6,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_tested_at TEXT, validation_criteria_json TEXT NOT NULL DEFAULT '[]', dependent_decision_ids_json TEXT NOT NULL DEFAULT '[]', evidence_count INTEGER NOT NULL DEFAULT 0, evidence_strength REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY(supersedes_theory_id) REFERENCES theories(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS theory_evidence (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    theory_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_episode_id TEXT,
    artifact_path TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0.7,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(theory_id) REFERENCES theories(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS memory_snapshots (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    snapshot_key TEXT NOT NULL,
    title TEXT NOT NULL,
    source_label TEXT NOT NULL DEFAULT 'manual',
    db_path TEXT,
    duckdb_path TEXT,
    parquet_dir TEXT,
    window_start TEXT,
    window_end TEXT,
    build_sha TEXT,
    build_branch TEXT,
    build_time TEXT,
    remote_host TEXT,
    table_counts_json TEXT NOT NULL DEFAULT '{}',
    total_rows INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    source_episode_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, snapshot_key),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS research_experiments (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    theory_id TEXT,
    snapshot_id TEXT,
    title TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    cohort_definition TEXT,
    success_criteria_json TEXT NOT NULL DEFAULT '{}',
    command TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    priority REAL NOT NULL DEFAULT 0.5,
    owner TEXT,
    due_at TEXT,
    source_episode_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(theory_id) REFERENCES theories(id),
    FOREIGN KEY(snapshot_id) REFERENCES memory_snapshots(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS experiment_results (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    theory_id TEXT,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    artifact_path TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    observed_at TEXT NOT NULL,
    source_episode_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES research_experiments(id),
    FOREIGN KEY(theory_id) REFERENCES theories(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS domain_concepts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'term',
    definition TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS research_insights (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    proposed_action TEXT,
    target_type TEXT,
    target_id TEXT,
    source_episode_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.6,
    status TEXT NOT NULL DEFAULT 'new',
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS workspace_manifest (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    workspace_id TEXT NOT NULL,
    db_uuid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_audit_at TEXT,
    last_audit_status TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
, last_repair_at TEXT);

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
    updated_at TEXT NOT NULL, source_type TEXT NOT NULL DEFAULT 'manual', source_id TEXT, reviewed_by TEXT, reviewed_at TEXT, expires_at TEXT, last_applied_at TEXT, application_count INTEGER NOT NULL DEFAULT 0, conflict_group TEXT, pinned INTEGER NOT NULL DEFAULT 0,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS vector_index_metadata (
    workspace_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    vector_backend TEXT NOT NULL,
    chunking_strategy TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, namespace)
);

CREATE TABLE IF NOT EXISTS memory_usage_feedback (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    usefulness REAL NOT NULL DEFAULT 0.0,
    task_id TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_state_snapshots (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    name          TEXT NOT NULL,
    taken_at      TEXT NOT NULL,
    counts_json   TEXT NOT NULL DEFAULT '{}',
    digests_json  TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    UNIQUE (workspace_id, name)
);

CREATE INDEX IF NOT EXISTS idx_agent_playbooks_workspace_name
    ON agent_playbooks(workspace_id, name, active);

CREATE INDEX IF NOT EXISTS idx_agent_roles_workspace_name
    ON agent_roles(workspace_id, name, active);

CREATE INDEX IF NOT EXISTS idx_agent_skills_workspace_name
    ON agent_skills(workspace_id, name, active);

CREATE INDEX IF NOT EXISTS idx_audit_workspace_created
    ON audit_log(workspace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_behavior_instructions_workspace_active
    ON behavior_instructions(workspace_id, active, priority, updated_at);

CREATE INDEX IF NOT EXISTS idx_behavior_instructions_workspace_conflict
    ON behavior_instructions(workspace_id, conflict_group, priority, updated_at);

CREATE INDEX IF NOT EXISTS idx_behavior_instructions_workspace_expires
    ON behavior_instructions(workspace_id, active, expires_at);

CREATE INDEX IF NOT EXISTS idx_behavior_instructions_workspace_kind
    ON behavior_instructions(workspace_id, kind, active);

CREATE INDEX IF NOT EXISTS idx_behavior_pinned
    ON behavior_instructions (workspace_id, pinned);

CREATE INDEX IF NOT EXISTS idx_capability_links_capability
    ON capability_links(workspace_id, capability_type, capability_id, strength);

CREATE INDEX IF NOT EXISTS idx_capability_links_target
    ON capability_links(workspace_id, target_type, target_id, strength);

CREATE INDEX IF NOT EXISTS idx_chunks_archived
    ON chunks (workspace_id, is_archived);

CREATE INDEX IF NOT EXISTS idx_chunks_workspace_episode
    ON chunks(workspace_id, episode_id);

CREATE INDEX IF NOT EXISTS idx_chunks_workspace_file
    ON chunks(workspace_id, file_id);

CREATE INDEX IF NOT EXISTS idx_core_pinned
    ON core_memory (workspace_id, pinned);

CREATE INDEX IF NOT EXISTS idx_decisions_active
    ON decisions(workspace_id, status, valid_to);

CREATE INDEX IF NOT EXISTS idx_decisions_pinned
    ON decisions (workspace_id, pinned);

CREATE INDEX IF NOT EXISTS idx_domain_concepts_workspace_name
    ON domain_concepts(workspace_id, name, active);

CREATE INDEX IF NOT EXISTS idx_entities_workspace_name
    ON entities(workspace_id, canonical_name);

CREATE INDEX IF NOT EXISTS idx_episodes_archived
    ON episodes (workspace_id, is_archived);

CREATE INDEX IF NOT EXISTS idx_episodes_workspace_created
    ON episodes(workspace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_episodes_workspace_task
    ON episodes(workspace_id, task_id);

CREATE INDEX IF NOT EXISTS idx_experiment_results_experiment
    ON experiment_results(workspace_id, experiment_id, observed_at);

CREATE INDEX IF NOT EXISTS idx_experiment_results_theory
    ON experiment_results(workspace_id, theory_id, observed_at);

CREATE INDEX IF NOT EXISTS idx_facts_active
    ON facts(workspace_id, valid_to, invalidated_by_fact_id);

CREATE INDEX IF NOT EXISTS idx_facts_workspace_relation
    ON facts(workspace_id, relation);

CREATE INDEX IF NOT EXISTS idx_facts_workspace_subject
    ON facts(workspace_id, subject_entity_id);

CREATE INDEX IF NOT EXISTS idx_files_archived
    ON files (workspace_id, is_archived);

CREATE INDEX IF NOT EXISTS idx_files_workspace_path
    ON files(workspace_id, path);

CREATE INDEX IF NOT EXISTS idx_maintenance_events_target
    ON maintenance_events(workspace_id, target_type, target_id);

CREATE INDEX IF NOT EXISTS idx_maintenance_events_workspace_status
    ON maintenance_events(workspace_id, status, severity, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_source
    ON memory_candidates(workspace_id, source_episode_id);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_workspace_status
    ON memory_candidates(workspace_id, status, importance, updated_at);

CREATE INDEX IF NOT EXISTS idx_memory_snapshots_workspace_key
    ON memory_snapshots(workspace_id, snapshot_key);

CREATE INDEX IF NOT EXISTS idx_memory_state_snapshots_ws_taken_at
    ON memory_state_snapshots (workspace_id, taken_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_usage_feedback_source
    ON memory_usage_feedback(workspace_id, source_type, source_id, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_usage_feedback_task
    ON memory_usage_feedback(workspace_id, task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_research_experiments_theory
    ON research_experiments(workspace_id, theory_id, status);

CREATE INDEX IF NOT EXISTS idx_research_experiments_workspace_status
    ON research_experiments(workspace_id, status, priority, updated_at);

CREATE INDEX IF NOT EXISTS idx_research_insights_workspace_status
    ON research_insights(workspace_id, status, insight_type, updated_at);

CREATE INDEX IF NOT EXISTS idx_theories_workspace_domain
    ON theories(workspace_id, domain, updated_at);

CREATE INDEX IF NOT EXISTS idx_theories_workspace_status
    ON theories(workspace_id, status, importance, updated_at);

CREATE INDEX IF NOT EXISTS idx_theory_evidence_theory
    ON theory_evidence(workspace_id, theory_id, observed_at);

CREATE INDEX IF NOT EXISTS idx_vector_index_metadata_workspace
    ON vector_index_metadata(workspace_id, namespace);
