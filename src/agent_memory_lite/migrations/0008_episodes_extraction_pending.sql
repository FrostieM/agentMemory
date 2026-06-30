-- 0008: defer the LLM auto-promote extraction off the synchronous episode write
-- (Batch E -- async persist-then-enqueue write path).
--
-- When MEMORY_DEFER_EXTRACTION is on, ingest_episode persists the episode and
-- marks extraction_pending = 1 INSTEAD of running the slow (Ollama-backed)
-- auto_promote extractor on the request thread. The brain-pass extraction-drain
-- step then runs the extractor asynchronously and clears the flag. This mirrors
-- the embedding_id-IS-NULL + idx_chunks_missing_embedding self-healing pattern
-- already used for deferred embeddings.
--
-- DEFAULT 0 so every PRE-EXISTING episode is "not pending": the drain never
-- re-extracts the whole history on first run; only NEW deferred episodes are
-- picked up. Forward-only.
ALTER TABLE episodes ADD COLUMN extraction_pending INTEGER NOT NULL DEFAULT 0;

-- Partial index over only the pending, non-archived rows: tiny (empty in the
-- steady state) so the brain-pass EXISTS probe and the ordered batch select are
-- O(matches), not O(workspace). The predicate mirrors the drain query exactly so
-- SQLite can use the index; archived episodes are excluded (never drained).
CREATE INDEX IF NOT EXISTS idx_episodes_extraction_pending
    ON episodes (workspace_id, created_at)
    WHERE extraction_pending = 1 AND is_archived = 0;
