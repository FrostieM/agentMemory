-- Retrieval usage feedback.
--
-- This table lets agents mark a returned memory item as helpful or noisy.
-- Ranking can then apply a small bounded boost/penalty on later retrievals.

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

CREATE INDEX IF NOT EXISTS idx_memory_usage_feedback_source
    ON memory_usage_feedback(workspace_id, source_type, source_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_usage_feedback_task
    ON memory_usage_feedback(workspace_id, task_id, created_at);
