-- agent-memory-lite v3.0.0 — consolidated initial schema.
--
-- Replaces the v2 migration chain (0001_init + 0020 through 0032).
-- One file lands the full v3 schema on a fresh database.
--
-- v3 changes vs v2:
--   * gist columns persisted on write (decisions, theories, behaviors,
--     skills, concepts, insights, tasks, code_digests). Read path
--     returns ~30-token gist by default; full body opt-in via fields=.
--   * versions table: immutable history for rollback. Replaces the
--     candidate-promote/reject hard gate for direct edits.
--   * skills table: merges v2 agent_roles + agent_skills + agent_playbooks
--     via subtype column. Body lives in body_md, fetched only on
--     memory_invoke_skill.
--   * behaviors table: renamed from behavior_instructions. Optionally
--     absorbs v2 core_memory + procedural_rules rows via kind column
--     during migrate_v2_to_v3.py (no separate tables in v3).
--   * code_digests table: renamed from file_digests. Adds structured
--     projection fields (top_symbols_json, top_callers_json,
--     top_callees_json, pagerank) for the agent surface.
--   * Renames: task_state → tasks, domain_concepts → concepts,
--     research_experiments → experiments, research_results → experiment_results,
--     research_insights → insights, memory_snapshots → snapshots,
--     memory_candidates → candidates.
--
-- Forward-only: scripts/migrate_v2_to_v3.py reads a v2 SQLite and
-- writes into the v3 schema below. This file is the only migration
-- needed for a fresh v3 database; later forward-only migrations
-- (0034_*, ...) chain over time.
--
-- FTS5 is synced application-side (see fts/chunks_fts.py); no
-- triggers are installed here.


-- ============================================================
-- FTS5 virtual table (unchanged from v2)
-- ============================================================

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    workspace_id UNINDEXED,
    path,
    symbols,
    text,
    summary,
    gist,
    tokenize = 'unicode61'
);


-- ============================================================
-- Workspace tracking
-- ============================================================

CREATE TABLE IF NOT EXISTS workspace_meta (
    workspace_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, key)
);

CREATE TABLE IF NOT EXISTS workspace_manifest (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    workspace_id TEXT NOT NULL,
    db_uuid TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'v3.0.0',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_audit_at TEXT,
    last_audit_status TEXT,
    last_repair_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);


-- ============================================================
-- Episodes (audit log, append-only)
-- ============================================================

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT,
    task_id TEXT,
    source_type TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    summary TEXT,
    gist TEXT,                              -- v3: ≤30 token one-liner for compact projection
    trust_level TEXT NOT NULL DEFAULT 'unknown',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    label TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_episodes_workspace_created
    ON episodes(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_episodes_workspace_task
    ON episodes(workspace_id, task_id);
CREATE INDEX IF NOT EXISTS idx_episodes_archived
    ON episodes(workspace_id, is_archived);


-- ============================================================
-- Tasks (renamed from task_state in v2)
-- ============================================================

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    goal_one_line TEXT,                     -- v3: compact projection
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


-- ============================================================
-- Plan steps (a task's plan as first-class, ordered rows)
-- ============================================================

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
    updated_at TEXT NOT NULL,
    FOREIGN KEY(parent_step_id) REFERENCES plan_steps(id),
    FOREIGN KEY(supersedes_step_id) REFERENCES plan_steps(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_plan_steps_task
    ON plan_steps(workspace_id, task_id, rank);
CREATE INDEX IF NOT EXISTS idx_plan_steps_status
    ON plan_steps(workspace_id, task_id, status);


-- ============================================================
-- Decisions (architectural choices)
-- ============================================================

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    decision_text TEXT NOT NULL,
    rationale TEXT,
    gist TEXT,                              -- v3: ~30-word compact projection
    status TEXT NOT NULL DEFAULT 'active',
    supersedes_decision_id TEXT,
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.9,
    importance REAL NOT NULL DEFAULT 0.8,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    feedback_ewma REAL NOT NULL DEFAULT 0.0,
    last_retrieved_at TEXT,
    references_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id),
    FOREIGN KEY(supersedes_decision_id) REFERENCES decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_active
    ON decisions(workspace_id, status, valid_to);
CREATE INDEX IF NOT EXISTS idx_decisions_pinned
    ON decisions(workspace_id, pinned);


-- ============================================================
-- Theories (research hypotheses)
-- ============================================================

CREATE TABLE IF NOT EXISTS theories (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'general',
    claim TEXT NOT NULL,
    gist TEXT,                              -- v3: ~30-word compact projection
    mechanism TEXT,
    predictions_json TEXT NOT NULL DEFAULT '[]',
    validation_criteria_json TEXT NOT NULL DEFAULT '[]',
    experiment_plan TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'proposed',
    supersedes_theory_id TEXT,
    dependent_decision_ids_json TEXT NOT NULL DEFAULT '[]',
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    importance REAL NOT NULL DEFAULT 0.6,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    evidence_strength REAL NOT NULL DEFAULT 0.0,
    feedback_ewma REAL NOT NULL DEFAULT 0.0,
    last_retrieved_at TEXT,
    last_tested_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(supersedes_theory_id) REFERENCES theories(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_theories_workspace_status
    ON theories(workspace_id, status, importance, updated_at);
CREATE INDEX IF NOT EXISTS idx_theories_workspace_domain
    ON theories(workspace_id, domain, updated_at);

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

CREATE INDEX IF NOT EXISTS idx_theory_evidence_theory
    ON theory_evidence(workspace_id, theory_id, observed_at);


-- ============================================================
-- Behaviors (v3: merges v2 behavior_instructions + core_memory + procedural_rules)
-- ============================================================
-- kind discriminator:
--   'core_memory'         - was core_memory.key/value
--   'procedural_rule'     - was procedural_rules.rule_text
--   'communication_style' | 'operating_rule' | 'project_convention' |
--   'workflow_preference' | 'role_guidance' - was behavior_instructions.kind

CREATE TABLE IF NOT EXISTS behaviors (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'workspace',
    priority TEXT NOT NULL DEFAULT 'user_preference',
    rule TEXT NOT NULL,                     -- the full instruction text
    rule_one_line TEXT,                     -- v3: compact one-line for projection
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
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_behaviors_workspace_active
    ON behaviors(workspace_id, active, priority, updated_at);
CREATE INDEX IF NOT EXISTS idx_behaviors_workspace_kind
    ON behaviors(workspace_id, kind, active);
CREATE INDEX IF NOT EXISTS idx_behaviors_pinned
    ON behaviors(workspace_id, pinned);
CREATE INDEX IF NOT EXISTS idx_behaviors_conflict_group
    ON behaviors(workspace_id, conflict_group, priority, updated_at);
CREATE INDEX IF NOT EXISTS idx_behaviors_expires
    ON behaviors(workspace_id, active, expires_at);


-- ============================================================
-- Skills (v3: merges v2 agent_roles + agent_skills + agent_playbooks via subtype)
-- ============================================================
-- subtype discriminator:
--   'role'     - was agent_roles (purpose, responsibilities_json, boundaries_json, handoff_triggers_json, tools_json)
--   'skill'    - was agent_skills (summary, when_to_use_json, inputs_json, outputs_json, tools_json, related_roles_json)
--   'playbook' - was agent_playbooks (goal, triggers_json, steps_json, success_criteria_json, required_skills_json)
-- All variants share: name, body_md (full content), when_to_use_short (≤20 token projection), counters.

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    subtype TEXT NOT NULL,                  -- 'role' | 'skill' | 'playbook'
    summary TEXT NOT NULL,
    when_to_use_short TEXT,                 -- v3: ≤20-token compact projection
    body_md TEXT NOT NULL DEFAULT '',       -- full markdown body, fetched only on memory_invoke_skill
    body_token_count INTEGER NOT NULL DEFAULT 0,
    -- variant-specific JSON payloads (all nullable; only relevant fields populated per subtype)
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
    -- common
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    active INTEGER NOT NULL DEFAULT 1,
    -- v1.5 capability maturity counters
    usage_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_invoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, subtype, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_skills_workspace_subtype
    ON skills(workspace_id, subtype, active, name);
CREATE INDEX IF NOT EXISTS idx_skills_workspace_recent
    ON skills(workspace_id, subtype, last_invoked_at DESC);


-- ============================================================
-- Concepts (renamed from domain_concepts in v2)
-- ============================================================

CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'term',
    definition TEXT NOT NULL,
    definition_one_line TEXT,               -- v3: compact projection
    aliases_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_episode_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.7,
    active INTEGER NOT NULL DEFAULT 1,
    last_retrieved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, name),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_concepts_workspace_name
    ON concepts(workspace_id, name, active);


-- ============================================================
-- Files + chunks (code memory substrate)
-- ============================================================

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_indexed_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE(workspace_id, path)
);

CREATE INDEX IF NOT EXISTS idx_files_workspace_path
    ON files(workspace_id, path);
CREATE INDEX IF NOT EXISTS idx_files_archived
    ON files(workspace_id, is_archived);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    file_id TEXT,
    episode_id TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    summary TEXT,
    gist TEXT,                              -- v3: compact projection for retrieval hits
    line_start INTEGER,
    line_end INTEGER,
    symbols_json TEXT NOT NULL DEFAULT '[]',
    symbol_kind TEXT,
    qualified_name TEXT,
    parent_qualified_name TEXT,
    embedding_id TEXT,
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,
    feedback_ewma REAL NOT NULL DEFAULT 0.0,
    last_retrieved_at TEXT,
    label TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(file_id) REFERENCES files(id),
    FOREIGN KEY(episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_workspace_file
    ON chunks(workspace_id, file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_workspace_episode
    ON chunks(workspace_id, episode_id);
CREATE INDEX IF NOT EXISTS idx_chunks_archived
    ON chunks(workspace_id, is_archived);
CREATE INDEX IF NOT EXISTS idx_chunks_qualified_name
    ON chunks(workspace_id, qualified_name)
    WHERE qualified_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chunks_symbol_kind
    ON chunks(workspace_id, symbol_kind, qualified_name)
    WHERE symbol_kind IS NOT NULL;


-- ============================================================
-- Code digests (renamed from file_digests, +structured projection fields)
-- ============================================================

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
    purpose_short TEXT,                     -- v3: ≤30-token one-line purpose
    narrative TEXT NOT NULL DEFAULT '',
    top_symbols_json TEXT NOT NULL DEFAULT '[]',    -- v3: [{name,kind,line,callers_count}]
    top_callers_json TEXT NOT NULL DEFAULT '[]',    -- v3: ["file:symbol", ...]
    top_callees_json TEXT NOT NULL DEFAULT '[]',    -- v3: same shape
    structured_json TEXT NOT NULL DEFAULT '{}',
    last_indexed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_code_digests_recent
    ON code_digests(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_code_digests_pagerank
    ON code_digests(workspace_id, pagerank DESC);


-- ============================================================
-- Symbol-level graph (v2 0029, 0030, 0031)
-- ============================================================

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

CREATE INDEX IF NOT EXISTS idx_symbol_edges_src
    ON symbol_edges(workspace_id, src_chunk_id);
CREATE INDEX IF NOT EXISTS idx_symbol_edges_dst_qname
    ON symbol_edges(workspace_id, dst_qualified_name, edge_type);
CREATE INDEX IF NOT EXISTS idx_symbol_edges_dst_chunk
    ON symbol_edges(workspace_id, dst_chunk_id)
    WHERE dst_chunk_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_symbol_edges_src_qname
    ON symbol_edges(workspace_id, src_qualified_name, edge_type);

CREATE TABLE IF NOT EXISTS symbol_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT,
    chunk_id TEXT,
    language TEXT,
    signature_text TEXT NOT NULL,
    signature_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbol_versions_qname
    ON symbol_versions(workspace_id, qualified_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_symbol_versions_recent
    ON symbol_versions(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_symbol_versions_signature
    ON symbol_versions(workspace_id, signature_hash);

CREATE TABLE IF NOT EXISTS active_edits (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    qualified_name TEXT,
    file_path TEXT,
    agent_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    note TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_active_edits_qname
    ON active_edits(workspace_id, qualified_name)
    WHERE qualified_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_active_edits_path
    ON active_edits(workspace_id, file_path)
    WHERE file_path IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_active_edits_expires
    ON active_edits(expires_at);

CREATE TABLE IF NOT EXISTS soft_edges (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    src_qualified_name TEXT NOT NULL,
    dst_qualified_name TEXT NOT NULL,
    edge_kind TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    observation_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_soft_edges_pair
    ON soft_edges(workspace_id, src_qualified_name, dst_qualified_name, edge_kind);
CREATE INDEX IF NOT EXISTS idx_soft_edges_src_weight
    ON soft_edges(workspace_id, src_qualified_name, edge_kind, weight DESC);


-- ============================================================
-- Research substrate (renamed from memory_snapshots / research_*)
-- ============================================================

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

CREATE INDEX IF NOT EXISTS idx_snapshots_workspace_key
    ON snapshots(workspace_id, snapshot_key);

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

CREATE INDEX IF NOT EXISTS idx_experiments_workspace_status
    ON experiments(workspace_id, status, priority, updated_at);
CREATE INDEX IF NOT EXISTS idx_experiments_theory
    ON experiments(workspace_id, theory_id, status);

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
    FOREIGN KEY(experiment_id) REFERENCES experiments(id),
    FOREIGN KEY(theory_id) REFERENCES theories(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_experiment_results_experiment
    ON experiment_results(workspace_id, experiment_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_experiment_results_theory
    ON experiment_results(workspace_id, theory_id, observed_at);

CREATE TABLE IF NOT EXISTS insights (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    gist TEXT,                              -- v3: compact projection
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

CREATE INDEX IF NOT EXISTS idx_insights_workspace_status
    ON insights(workspace_id, status, insight_type, updated_at);


-- ============================================================
-- Capability links (target -> skill/role/playbook relation)
-- ============================================================

CREATE TABLE IF NOT EXISTS capability_links (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    capability_type TEXT NOT NULL,          -- 'role' | 'skill' | 'playbook' (v2 names preserved)
    capability_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    relation TEXT NOT NULL,
    rationale TEXT,
    strength REAL NOT NULL DEFAULT 0.7,
    source_episode_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, target_type, target_id, capability_type, capability_id, relation),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_capability_links_target
    ON capability_links(workspace_id, target_type, target_id, strength);
CREATE INDEX IF NOT EXISTS idx_capability_links_capability
    ON capability_links(workspace_id, capability_type, capability_id, strength);


-- ============================================================
-- Candidates (correction loop + decision/insight proposals)
-- ============================================================

-- Generic candidates (was memory_candidates) — correction loop primary use.
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,                     -- 'correction' | 'decision' | 'rule' | ...
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

CREATE INDEX IF NOT EXISTS idx_candidates_workspace_status
    ON candidates(workspace_id, status, importance, updated_at);
CREATE INDEX IF NOT EXISTS idx_candidates_source
    ON candidates(workspace_id, source_episode_id);

-- Theory -> decision-candidate bridge (v1.7).
CREATE TABLE IF NOT EXISTS decision_candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    theory_id TEXT NOT NULL,
    proposed_title TEXT NOT NULL,
    proposed_decision_text TEXT NOT NULL,
    proposed_rationale TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    evidence_strength REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 0.7,
    status TEXT NOT NULL DEFAULT 'pending',
    promoted_decision_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    FOREIGN KEY(theory_id) REFERENCES theories(id),
    FOREIGN KEY(promoted_decision_id) REFERENCES decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_decision_candidates_workspace_status
    ON decision_candidates(workspace_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_decision_candidates_theory
    ON decision_candidates(theory_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_decision_candidates_open_per_theory
    ON decision_candidates(theory_id) WHERE status = 'pending';

-- Reflective-compaction lesson candidates (v1.8).
CREATE TABLE IF NOT EXISTS insight_candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    proposed_action TEXT,
    target_type TEXT,
    target_id TEXT,
    source_episode_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.6,
    status TEXT NOT NULL DEFAULT 'pending',
    promoted_insight_id TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    FOREIGN KEY(promoted_insight_id) REFERENCES insights(id)
);

CREATE INDEX IF NOT EXISTS idx_insight_candidates_workspace_status
    ON insight_candidates(workspace_id, status, updated_at);


-- ============================================================
-- Versions (v3 new: immutable rollback history for any kind)
-- ============================================================

CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY,                    -- ver_<uuid>
    workspace_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,              -- 'decision' | 'theory' | 'behavior' | 'skill' | ...
    target_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,            -- monotone per target_id
    content_snapshot_json TEXT NOT NULL,    -- full row contents at write time
    content_sha256 TEXT NOT NULL,
    actor TEXT NOT NULL,                    -- agent_id or 'operator'
    why TEXT,                               -- required on rollback
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, target_kind, target_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_versions_target
    ON versions(workspace_id, target_kind, target_id, version_no DESC);
CREATE INDEX IF NOT EXISTS idx_versions_recent
    ON versions(workspace_id, created_at DESC);


-- ============================================================
-- Audit log (kept from v2 + agent_id)
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    source_episode_id TEXT,
    agent_id TEXT,                          -- v1.3
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_workspace_created
    ON audit_log(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_agent_id_workspace
    ON audit_log(workspace_id, agent_id, created_at);


-- ============================================================
-- Maintenance + sentinel + feedback
-- ============================================================

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
    recurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT,
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_maintenance_events_workspace_status
    ON maintenance_events(workspace_id, status, severity, created_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_target
    ON maintenance_events(workspace_id, target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_dedup
    ON maintenance_events(workspace_id, kind, target_type, target_id, status);

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

CREATE INDEX IF NOT EXISTS idx_retrieval_sentinel_results_workspace_case
    ON retrieval_sentinel_results(workspace_id, case_name, created_at);
CREATE INDEX IF NOT EXISTS idx_retrieval_sentinel_results_run
    ON retrieval_sentinel_results(run_id);

CREATE TABLE IF NOT EXISTS memory_usage_feedback (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    usefulness REAL NOT NULL DEFAULT 0.0,
    task_id TEXT,
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent_observed',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_usage_feedback_source
    ON memory_usage_feedback(workspace_id, source_type, source_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_usage_feedback_task
    ON memory_usage_feedback(workspace_id, task_id, created_at);

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


-- ============================================================
-- Vector + state snapshot tracking
-- ============================================================

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

CREATE INDEX IF NOT EXISTS idx_vector_index_metadata_workspace
    ON vector_index_metadata(workspace_id, namespace);

CREATE TABLE IF NOT EXISTS memory_state_snapshots (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}',
    digests_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, name)
);

CREATE INDEX IF NOT EXISTS idx_memory_state_snapshots_ws_taken_at
    ON memory_state_snapshots(workspace_id, taken_at DESC);


-- ============================================================
-- Entities + facts (v2 graph layer — kept for compat; review for removal v3.1)
-- ============================================================

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

CREATE INDEX IF NOT EXISTS idx_entities_workspace_name
    ON entities(workspace_id, canonical_name);

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

CREATE INDEX IF NOT EXISTS idx_facts_workspace_subject
    ON facts(workspace_id, subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_facts_workspace_relation
    ON facts(workspace_id, relation);
CREATE INDEX IF NOT EXISTS idx_facts_active
    ON facts(workspace_id, valid_to, invalidated_by_fact_id);


-- ============================================================
-- FTS5 shadow tables (created automatically by chunks_fts above)
-- Declared explicitly for IF NOT EXISTS safety on re-runs.
-- ============================================================

CREATE TABLE IF NOT EXISTS 'chunks_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
CREATE TABLE IF NOT EXISTS 'chunks_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS 'chunks_fts_content'(id INTEGER PRIMARY KEY, c0, c1, c2, c3, c4, c5, c6);
CREATE TABLE IF NOT EXISTS 'chunks_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE IF NOT EXISTS 'chunks_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
