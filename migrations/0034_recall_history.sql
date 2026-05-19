-- v3.1 Vector 2 — Adaptive retrieval observability table.
--
-- ``recall_history`` logs every recall invocation with the params used
-- and the outcome stats of the returned hit set. The Vector 2 adapter
-- (``retrieval/recall_tuning.py``) rolls this up to suggest workspace-
-- specific defaults for ``depth`` and ``outcome_floor`` so the agent
-- doesn't pay full breadth when shallow recall already converges.
--
-- The table is observability, not state — recall continues to work if
-- ``MEMORY_RECALL_TUNING_ENABLED=false`` (no rows written). Adaptive
-- suggestion is a pure read over this rollup; the agent can still
-- override per-call by passing explicit ``depth`` / ``outcome_floor``.
--
-- Schema:
--   * ``workspace_id`` / ``topic_norm`` — partition key for rollup
--     (topic_norm = lower(strip(topic)), capped at 80 chars).
--   * ``depth`` / ``outcome_floor_x100`` — params used (floor stored
--     as int*100 to allow integer indexing).
--   * ``hits_count`` — len(result) for "did we find anything?" stats.
--   * ``avg_outcome_x100`` — mean outcome_score across returned hits,
--     int*100 (NULL when hits_count = 0).
--   * ``avg_activation_x1000`` — mean activation int*1000 (proxy for
--     spread quality).
--   * ``created_at`` — iso_now stamp.
--
-- Index supports the rollup query: ``WHERE workspace_id = ? AND
-- created_at >= ? GROUP BY depth, outcome_floor_x100``.

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

CREATE INDEX IF NOT EXISTS idx_recall_history_workspace_created_at
    ON recall_history (workspace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_recall_history_workspace_topic
    ON recall_history (workspace_id, topic_norm, created_at);
