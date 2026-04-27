-- Research-lab memory layer.
--
-- Theories hold claims. This migration adds the operational research objects
-- that turn those claims into repeatable work: data snapshots, experiments,
-- experiment results, domain concepts, and distilled insights.

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

CREATE INDEX IF NOT EXISTS idx_memory_snapshots_workspace_key
    ON memory_snapshots(workspace_id, snapshot_key);
CREATE INDEX IF NOT EXISTS idx_research_experiments_workspace_status
    ON research_experiments(workspace_id, status, priority, updated_at);
CREATE INDEX IF NOT EXISTS idx_research_experiments_theory
    ON research_experiments(workspace_id, theory_id, status);
CREATE INDEX IF NOT EXISTS idx_experiment_results_experiment
    ON experiment_results(workspace_id, experiment_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_experiment_results_theory
    ON experiment_results(workspace_id, theory_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_domain_concepts_workspace_name
    ON domain_concepts(workspace_id, name, active);
CREATE INDEX IF NOT EXISTS idx_research_insights_workspace_status
    ON research_insights(workspace_id, status, insight_type, updated_at);
