-- Phase 2 of the "memory as organ" evolution: Hebbian co-retrieval associations.
-- Every memory_search call logs the items it returned together; a sentinel-
-- scheduled pass distills co-occurrence into weighted soft_edges. With the
-- HeLa-Mem outcome gate, we skip strengthening pairs both rooted in failure.
--
-- Companion modules:
--   src/agent_memory_lite/retrieval/coactivation_log.py   (log_coactivation)
--   src/agent_memory_lite/maintenance/hebbian_pass.py     (distill into edges)
--   src/agent_memory_lite/retrieval/spreading_activation.py (BFS read path)
--
-- soft_edges (from 0001_init.sql) already has the shape we need; Phase 2
-- extends the ALLOWED_SOFT_KINDS allowlist in Python with two new edge_kinds:
--   - co_retrieved   (returned in same search hits set)
--   - co_mentioned   (mentioned together in an episode body)
-- No DDL change needed for soft_edges itself.

-- The coactivation log is the staging table. Each search appends one row per
-- hit; the Hebbian pass reads, distills into soft_edges, then prunes rows
-- older than MEMORY_HEBBIAN_LOG_RETENTION_DAYS.
CREATE TABLE IF NOT EXISTS retrieval_coactivation (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    query_hash    TEXT NOT NULL,
    item_kind     TEXT NOT NULL,
    item_id       TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coact_workspace_hash
    ON retrieval_coactivation(workspace_id, query_hash, rank);
CREATE INDEX IF NOT EXISTS idx_coact_workspace_created
    ON retrieval_coactivation(workspace_id, created_at);
