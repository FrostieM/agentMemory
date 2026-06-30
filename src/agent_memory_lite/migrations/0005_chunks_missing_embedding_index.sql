-- 0005: partial index that makes incremental vector-repair detection cheap
-- (release plan step 7).
--
-- The brain-pass vector-repair step probes for chunks whose vector never
-- landed (embedding_id IS NULL) and re-embeds a bounded batch. Without an
-- index the EXISTS probe full-scans the workspace's chunks on every pass in
-- the healthy (zero-missing) steady state. A PARTIAL index over only the
-- missing, non-archived rows is tiny (empty once the store is healthy) and
-- makes both the probe and the ordered batch select O(matches). The predicate
-- mirrors the repair query exactly so SQLite can use it; archived/soft-deleted
-- chunks are excluded because the repair step does not re-vector them.
CREATE INDEX IF NOT EXISTS idx_chunks_missing_embedding
    ON chunks (workspace_id, created_at)
    WHERE embedding_id IS NULL AND is_archived = 0;
