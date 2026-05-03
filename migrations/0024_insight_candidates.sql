-- agent-memory-lite v1.8 — reflective-compaction lesson candidates.
--
-- Reflective compaction (compaction/lesson_extractor.py) runs an Ollama pass
-- over old episodes and proposes "lessons" the agent could distill into
-- research_insights. The proposals are NEVER written into research_insights
-- automatically — they land here, and the operator either accepts (which
-- creates an actual research_insight row) or rejects (which keeps the row
-- as audit evidence with status='rejected').
--
-- Trust-gate invariant: even when the env flag is on, no insight ever
-- bypasses operator review. The shape mirrors research_insights so that
-- accept can be a single INSERT with the same fields.

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
    FOREIGN KEY(promoted_insight_id) REFERENCES research_insights(id)
);

CREATE INDEX IF NOT EXISTS idx_insight_candidates_workspace_status
    ON insight_candidates(workspace_id, status, updated_at);
