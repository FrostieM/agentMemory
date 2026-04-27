-- First-class research theories and evidence.
--
-- Episodes are a chronological audit log. Theories are the working scientific
-- layer: claims, mechanisms, predictions, experiments, and evidence that can
-- accumulate without being buried inside raw session history.

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
    last_tested_at TEXT,
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

CREATE INDEX IF NOT EXISTS idx_theories_workspace_status
    ON theories(workspace_id, status, importance, updated_at);
CREATE INDEX IF NOT EXISTS idx_theories_workspace_domain
    ON theories(workspace_id, domain, updated_at);
CREATE INDEX IF NOT EXISTS idx_theory_evidence_theory
    ON theory_evidence(workspace_id, theory_id, observed_at);
