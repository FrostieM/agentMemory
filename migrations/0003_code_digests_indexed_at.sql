-- 0003_code_digests_indexed_at.sql
-- Composite index for the durable digest-refresh worker (plan step 10.5).
-- That worker selects the least-recently-verified digests per workspace on
-- every brain pass:
--     WHERE workspace_id = ? ORDER BY last_indexed_at ASC, file_path ASC LIMIT ?
-- Without this index SQLite builds a TEMP B-TREE and sorts the whole
-- workspace's digest set each tick; the index makes the ORDER BY + LIMIT
-- index-served. Forward-only, additive, idempotent (IF NOT EXISTS).
CREATE INDEX IF NOT EXISTS idx_code_digests_ws_indexed
    ON code_digests (workspace_id, last_indexed_at, file_path);
