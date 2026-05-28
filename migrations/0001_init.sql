-- agent-memory-lite v3 canonical initial schema.
-- Fresh databases start directly on the compact v3 surface.
-- Legacy v1/v2 storage tables are intentionally absent. Databases that
-- already marked 0001_init applied must match this canonical v3 baseline;
-- hybrid historical-chain databases are refused by the migration runner.

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
, agent_id TEXT);

CREATE TABLE IF NOT EXISTS behaviors (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'workspace',
    priority TEXT NOT NULL DEFAULT 'user_preference',
    rule TEXT NOT NULL,
    rule_one_line TEXT,
    rationale TEXT NOT NULL DEFAULT '',
    applies_to_json TEXT NOT NULL DEFAULT '[]',
    conflict_policy TEXT NOT NULL DEFAULT 'current_user_wins',
    conflict_group TEXT,
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_id TEXT,
    source_episode_id TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    expires_at TEXT,
    confidence REAL NOT NULL DEFAULT 0.85,
    importance REAL NOT NULL DEFAULT 0.5,
    pinned INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    last_applied_at TEXT,
    application_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    outcome_score REAL NOT NULL DEFAULT 0.0,
    valid_from TEXT,
    valid_to TEXT,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS candidates (
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
    source_episode_id TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    promoted_target_type TEXT,
    promoted_target_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
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

CREATE TABLE IF NOT EXISTS causal_links (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    src_kind TEXT NOT NULL,
    src_id TEXT NOT NULL,
    dst_kind TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    evidence_episode_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, src_kind, src_id, dst_kind, dst_id, relation)
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    file_id TEXT,
    episode_id TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    gist TEXT,
    summary TEXT,
    line_start INTEGER,
    line_end INTEGER,
    symbols_json TEXT NOT NULL DEFAULT '[]',
    embedding_id TEXT,
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,
    outcome_score REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}', label TEXT, is_archived INTEGER NOT NULL DEFAULT 0, feedback_ewma REAL NOT NULL DEFAULT 0.0, last_retrieved_at TEXT, symbol_kind TEXT, qualified_name TEXT, parent_qualified_name TEXT,
    FOREIGN KEY(file_id) REFERENCES files(id),
    FOREIGN KEY(episode_id) REFERENCES episodes(id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    workspace_id UNINDEXED,
    path,
    symbols,
    text,
    summary,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS code_digests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_sha1 TEXT,
    language TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    symbol_count INTEGER NOT NULL DEFAULT 0,
    inbound_edge_count INTEGER NOT NULL DEFAULT 0,
    outbound_edge_count INTEGER NOT NULL DEFAULT 0,
    versions_recent INTEGER NOT NULL DEFAULT 0,
    pagerank REAL NOT NULL DEFAULT 0.0,
    purpose_short TEXT,
    narrative TEXT NOT NULL DEFAULT '',
    top_symbols_json TEXT NOT NULL DEFAULT '[]',
    top_callers_json TEXT NOT NULL DEFAULT '[]',
    top_callees_json TEXT NOT NULL DEFAULT '[]',
    structured_json TEXT NOT NULL DEFAULT '{}',
    last_indexed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, file_path)
);

CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'term',
    definition TEXT NOT NULL,
    definition_one_line TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    active INTEGER NOT NULL DEFAULT 1,
    last_retrieved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    decision_text TEXT NOT NULL,
    gist TEXT,
    rationale TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    supersedes_decision_id TEXT,
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.9,
    importance REAL NOT NULL DEFAULT 0.8,
    outcome_score REAL NOT NULL DEFAULT 0.0,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, feedback_ewma REAL NOT NULL DEFAULT 0.0, last_retrieved_at TEXT, references_json TEXT,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id),
    FOREIGN KEY(supersedes_decision_id) REFERENCES decisions(id)
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

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT,
    task_id TEXT,
    source_type TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    gist TEXT,
    summary TEXT,
    trust_level TEXT NOT NULL DEFAULT 'unknown',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
, label TEXT, is_archived INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS "experiment_results" (
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
    FOREIGN KEY(experiment_id) REFERENCES experiments(id),
    FOREIGN KEY(theory_id) REFERENCES theories(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS experiments (
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
    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
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

CREATE TABLE IF NOT EXISTS insights (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    gist TEXT,
    proposed_action TEXT,
    target_type TEXT,
    target_id TEXT,
    source_episode_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.6,
    status TEXT NOT NULL DEFAULT 'new',
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    outcome_score REAL NOT NULL DEFAULT 0.0,
    last_surfaced_at TEXT,
    surface_count INTEGER NOT NULL DEFAULT 0,
    valid_from TEXT,
    valid_to TEXT
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
    resolved_at TEXT, recurrence_count INTEGER NOT NULL DEFAULT 1, first_seen_at TEXT, last_seen_at TEXT, action_status TEXT NOT NULL DEFAULT 'open', assigned_to TEXT, action_notes TEXT, claimed_at TEXT, dismissed_at TEXT,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS memory_disputes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    claimant_agent_id TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'open',
    resolution TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
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
, source TEXT NOT NULL DEFAULT 'agent_observed');

CREATE TABLE IF NOT EXISTS plan_steps (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    parent_step_id TEXT,
    rank REAL NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'done', 'blocked', 'skipped')),
    supersedes_step_id TEXT,
    source_episode_id TEXT,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, outcome_fed_at TEXT,
    FOREIGN KEY(parent_step_id) REFERENCES plan_steps(id),
    FOREIGN KEY(supersedes_step_id) REFERENCES plan_steps(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS recall_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    topic_norm TEXT NOT NULL,
    depth INTEGER NOT NULL,
    outcome_floor_x100 INTEGER NOT NULL,
    hits_count INTEGER NOT NULL,
    avg_outcome_x100 INTEGER,
    avg_activation_x1000 INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reflex_rules (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    trigger_tool TEXT NOT NULL,
    trigger_pattern TEXT NOT NULL DEFAULT '',
    precondition_kind TEXT NOT NULL,
    precondition_param_json TEXT NOT NULL DEFAULT '{}',
    enforcement TEXT NOT NULL DEFAULT 'advisory',
    derived_from_insight_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    block_count INTEGER NOT NULL DEFAULT 0,
    advisory_count INTEGER NOT NULL DEFAULT 0,
    last_fired_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, rule_name)
);

CREATE TABLE IF NOT EXISTS retrieval_coactivation (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_sentinel_results (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    case_name TEXT NOT NULL,
    status TEXT NOT NULL,
    matched_count INTEGER NOT NULL DEFAULT 0,
    expected_count INTEGER NOT NULL DEFAULT 0,
    failures_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    run_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS self_model (
    workspace_id TEXT PRIMARY KEY,
    identity_text TEXT NOT NULL,
    invariants_json TEXT NOT NULL DEFAULT '[]',
    uncertainties_json TEXT NOT NULL DEFAULT '[]',
    source_decision_ids_json TEXT NOT NULL DEFAULT '[]',
    coverage_score REAL NOT NULL DEFAULT 0.0,
    refreshed_via TEXT NOT NULL DEFAULT 'heuristic',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    subtype TEXT NOT NULL,
    summary TEXT NOT NULL,
    when_to_use_short TEXT,
    body_md TEXT NOT NULL DEFAULT '',
    body_token_count INTEGER NOT NULL DEFAULT 0,
    when_to_use_json TEXT,
    inputs_json TEXT,
    outputs_json TEXT,
    tools_json TEXT,
    related_roles_json TEXT,
    responsibilities_json TEXT,
    boundaries_json TEXT,
    handoff_triggers_json TEXT,
    triggers_json TEXT,
    steps_json TEXT,
    success_criteria_json TEXT,
    required_skills_json TEXT,
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    active INTEGER NOT NULL DEFAULT 1,
    usage_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_invoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    outcome_score REAL NOT NULL DEFAULT 0.0,
    UNIQUE(workspace_id, subtype, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS snapshots (
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

CREATE TABLE IF NOT EXISTS soft_edges (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    src_qualified_name TEXT NOT NULL,
    dst_qualified_name TEXT NOT NULL,
    edge_kind TEXT NOT NULL,        -- 'co_changed' | 'co_referenced' | 'similar_signature'
    weight REAL NOT NULL DEFAULT 1.0,
    observation_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS symbol_edges (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    src_chunk_id TEXT NOT NULL,
    src_qualified_name TEXT NOT NULL,
    dst_qualified_name TEXT NOT NULL,
    dst_chunk_id TEXT,
    edge_type TEXT NOT NULL,
    src_language TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS symbol_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT,
    chunk_id TEXT,                  -- live link at version creation; may go stale
    language TEXT,
    signature_text TEXT NOT NULL,   -- first line of the chunk body
    signature_hash TEXT NOT NULL,   -- blake2b of signature_text
    content_hash TEXT NOT NULL,     -- blake2b of full chunk body
    created_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    goal_one_line TEXT,
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

CREATE TABLE IF NOT EXISTS theories (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'general',
    claim TEXT NOT NULL,
    gist TEXT,
    mechanism TEXT,
    predictions_json TEXT NOT NULL DEFAULT '[]',
    experiment_plan TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'proposed',
    supersedes_theory_id TEXT,
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    importance REAL NOT NULL DEFAULT 0.6,
    outcome_score REAL NOT NULL DEFAULT 0.0,
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_tested_at TEXT, validation_criteria_json TEXT NOT NULL DEFAULT '[]', dependent_decision_ids_json TEXT NOT NULL DEFAULT '[]', evidence_count INTEGER NOT NULL DEFAULT 0, evidence_strength REAL NOT NULL DEFAULT 0.0, feedback_ewma REAL NOT NULL DEFAULT 0.0, last_retrieved_at TEXT,
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

CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    content_snapshot_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    actor TEXT NOT NULL,
    why TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, target_kind, target_id, version_no)
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

CREATE TABLE IF NOT EXISTS workspace_meta (
    workspace_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, key)
);

CREATE VIEW IF NOT EXISTS memory_usage_feedback_summary AS
SELECT
    workspace_id,
    source_type,
    source_id,
    SUM(usefulness) AS usefulness_sum,
    AVG(usefulness) AS usefulness_avg,
    COUNT(*) AS feedback_count,
    MAX(created_at) AS last_feedback_at,
    MIN(created_at) AS first_feedback_at
FROM memory_usage_feedback
GROUP BY workspace_id, source_type, source_id;

CREATE INDEX IF NOT EXISTS idx_audit_log_agent_id_workspace
    ON audit_log (workspace_id, agent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_audit_workspace_created
    ON audit_log(workspace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_behaviors_outcome
    ON behaviors(workspace_id, active, outcome_score DESC);

CREATE INDEX IF NOT EXISTS idx_behaviors_validity
    ON behaviors(workspace_id, valid_to);

CREATE INDEX IF NOT EXISTS idx_capability_links_capability
    ON capability_links(workspace_id, capability_type, capability_id, strength);

CREATE INDEX IF NOT EXISTS idx_capability_links_target
    ON capability_links(workspace_id, target_type, target_id, strength);

CREATE INDEX IF NOT EXISTS idx_causal_dst
    ON causal_links(workspace_id, dst_kind, dst_id);

CREATE INDEX IF NOT EXISTS idx_causal_relation
    ON causal_links(workspace_id, relation, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_causal_src
    ON causal_links(workspace_id, src_kind, src_id);

CREATE INDEX IF NOT EXISTS idx_chunks_archived
    ON chunks (workspace_id, is_archived);

CREATE INDEX IF NOT EXISTS idx_chunks_episode_id
    ON chunks(episode_id);

CREATE INDEX IF NOT EXISTS idx_chunks_file_id
    ON chunks(file_id);

CREATE INDEX IF NOT EXISTS idx_chunks_qualified_name
    ON chunks (workspace_id, qualified_name)
    WHERE qualified_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_symbol_kind
    ON chunks (workspace_id, symbol_kind, qualified_name)
    WHERE symbol_kind IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_workspace_episode
    ON chunks(workspace_id, episode_id);

CREATE INDEX IF NOT EXISTS idx_chunks_workspace_file
    ON chunks(workspace_id, file_id);

CREATE INDEX IF NOT EXISTS idx_coact_workspace_created
    ON retrieval_coactivation(workspace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_coact_workspace_hash
    ON retrieval_coactivation(workspace_id, query_hash, rank);

CREATE INDEX IF NOT EXISTS idx_concepts_validity
    ON concepts(workspace_id, valid_to);

CREATE INDEX IF NOT EXISTS idx_candidates_workspace_status_kind
    ON candidates(workspace_id, status, kind, updated_at);

CREATE INDEX IF NOT EXISTS idx_decisions_active
    ON decisions(workspace_id, status, valid_to);

CREATE INDEX IF NOT EXISTS idx_decisions_outcome
    ON decisions(workspace_id, status, outcome_score DESC);

CREATE INDEX IF NOT EXISTS idx_decisions_pinned
    ON decisions (workspace_id, pinned);

CREATE INDEX IF NOT EXISTS idx_decisions_source_episode_id
    ON decisions(source_episode_id);

CREATE INDEX IF NOT EXISTS idx_decisions_supersedes_decision_id
    ON decisions(supersedes_decision_id);

CREATE INDEX IF NOT EXISTS idx_decisions_workspace_status_updated_at
    ON decisions (workspace_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_decisions_workspace_status_valid_from
    ON decisions (workspace_id, status, valid_from);

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

CREATE INDEX IF NOT EXISTS idx_experiment_results_experiment_id
    ON experiment_results(experiment_id);

CREATE INDEX IF NOT EXISTS idx_experiment_results_theory
    ON experiment_results(workspace_id, theory_id, observed_at);

CREATE INDEX IF NOT EXISTS idx_experiment_results_theory_id
    ON experiment_results(theory_id);

CREATE INDEX IF NOT EXISTS idx_facts_active
    ON facts(workspace_id, valid_to, invalidated_by_fact_id);

CREATE INDEX IF NOT EXISTS idx_facts_object_entity_id
    ON facts(object_entity_id);

CREATE INDEX IF NOT EXISTS idx_facts_subject_entity_id
    ON facts(subject_entity_id);

CREATE INDEX IF NOT EXISTS idx_facts_workspace_relation
    ON facts(workspace_id, relation);

CREATE INDEX IF NOT EXISTS idx_facts_workspace_subject
    ON facts(workspace_id, subject_entity_id);

CREATE INDEX IF NOT EXISTS idx_files_archived
    ON files (workspace_id, is_archived);

CREATE INDEX IF NOT EXISTS idx_files_workspace_path
    ON files(workspace_id, path);

CREATE INDEX IF NOT EXISTS idx_insights_outcome
    ON insights(workspace_id, status, outcome_score DESC);

CREATE INDEX IF NOT EXISTS idx_insights_validity
    ON insights(workspace_id, valid_to);

CREATE INDEX IF NOT EXISTS idx_maintenance_events_action_queue
    ON maintenance_events(workspace_id, action_status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_maintenance_events_dedup
    ON maintenance_events(workspace_id, kind, target_type, target_id, status);

CREATE INDEX IF NOT EXISTS idx_maintenance_events_target
    ON maintenance_events(workspace_id, target_type, target_id);

CREATE INDEX IF NOT EXISTS idx_maintenance_events_workspace_status
    ON maintenance_events(workspace_id, status, severity, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_disputes_target
    ON memory_disputes(workspace_id, target_kind, target_id);

CREATE INDEX IF NOT EXISTS idx_memory_disputes_workspace_status
    ON memory_disputes(workspace_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_usage_feedback_source
    ON memory_usage_feedback(workspace_id, source_type, source_id, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_usage_feedback_task
    ON memory_usage_feedback(workspace_id, task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_plan_steps_status
    ON plan_steps(workspace_id, task_id, status);

CREATE INDEX IF NOT EXISTS idx_plan_steps_task
    ON plan_steps(workspace_id, task_id, rank);

CREATE INDEX IF NOT EXISTS idx_recall_history_workspace_created_at
    ON recall_history (workspace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_recall_history_workspace_topic
    ON recall_history (workspace_id, topic_norm, created_at);

CREATE INDEX IF NOT EXISTS idx_reflex_rules_active
    ON reflex_rules(workspace_id, active, trigger_tool);

CREATE INDEX IF NOT EXISTS idx_reflex_rules_insight
    ON reflex_rules(workspace_id, derived_from_insight_id);

CREATE INDEX IF NOT EXISTS idx_retrieval_sentinel_results_run
    ON retrieval_sentinel_results(run_id);

CREATE INDEX IF NOT EXISTS idx_retrieval_sentinel_results_workspace_case
    ON retrieval_sentinel_results(workspace_id, case_name, created_at);

CREATE INDEX IF NOT EXISTS idx_skills_outcome
    ON skills(workspace_id, active, outcome_score DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_soft_edges_pair
    ON soft_edges (workspace_id, src_qualified_name, dst_qualified_name, edge_kind);

CREATE INDEX IF NOT EXISTS idx_soft_edges_src_weight
    ON soft_edges (workspace_id, src_qualified_name, edge_kind, weight DESC);

CREATE INDEX IF NOT EXISTS idx_symbol_edges_dst_chunk
    ON symbol_edges (workspace_id, dst_chunk_id)
    WHERE dst_chunk_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_symbol_edges_dst_qname
    ON symbol_edges (workspace_id, dst_qualified_name, edge_type);

CREATE INDEX IF NOT EXISTS idx_symbol_edges_src
    ON symbol_edges (workspace_id, src_chunk_id);

CREATE INDEX IF NOT EXISTS idx_symbol_edges_src_qname
    ON symbol_edges (workspace_id, src_qualified_name, edge_type);

CREATE INDEX IF NOT EXISTS idx_symbol_versions_qname
    ON symbol_versions (workspace_id, qualified_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_symbol_versions_recent
    ON symbol_versions (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_symbol_versions_signature
    ON symbol_versions (workspace_id, signature_hash);

CREATE INDEX IF NOT EXISTS idx_theories_outcome
    ON theories(workspace_id, status, outcome_score DESC);

CREATE INDEX IF NOT EXISTS idx_theories_validity
    ON theories(workspace_id, valid_to);

CREATE INDEX IF NOT EXISTS idx_theories_workspace_domain
    ON theories(workspace_id, domain, updated_at);

CREATE INDEX IF NOT EXISTS idx_theories_workspace_status
    ON theories(workspace_id, status, importance, updated_at);

CREATE INDEX IF NOT EXISTS idx_theory_evidence_theory
    ON theory_evidence(workspace_id, theory_id, observed_at);

CREATE INDEX IF NOT EXISTS idx_theory_evidence_theory_id
    ON theory_evidence(theory_id);

CREATE INDEX IF NOT EXISTS idx_vector_index_metadata_workspace
    ON vector_index_metadata(workspace_id, namespace);

CREATE INDEX IF NOT EXISTS idx_versions_recent
    ON versions(workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_versions_target
    ON versions(workspace_id, target_kind, target_id, version_no DESC);
