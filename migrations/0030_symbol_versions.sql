-- 1.6.0: symbol-level version history + breaking-change detection.
--
-- Pre-1.6.0 every file re-ingest dropped the old chunks and wrote
-- new ones; there was no record that ``paperBot.calculate``'s
-- signature changed at 14:32 yesterday from ``calculate(self, x)``
-- to ``calculate(self, x, y=None)``. This table records every
-- version of every symbol so an agent can answer:
--
--   * "what changed in paperBot.calculate over the last 7 days?"
--   * "which active symbols had a SIGNATURE change recently?"
--     (paired with graph_neighbors → list of breaking changes)
--
-- We store the signature text inline (not just its hash) so the
-- history endpoint can render a diff even after the underlying
-- chunk is deleted on re-ingest.
--
-- Lifecycle: a new row is appended every time a chunk's
-- content_hash differs from the most recent version for the same
-- (workspace_id, qualified_name). chunks may come and go; the
-- version chain is durable.
CREATE TABLE IF NOT EXISTS symbol_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT,
    chunk_id TEXT,                  -- live link at version creation; may go stale
    language TEXT,
    signature_text TEXT NOT NULL,   -- first line of the chunk body
    signature_hash TEXT NOT NULL,   -- blake2b of signature_text
    content_hash TEXT NOT NULL,     -- blake2b of full chunk body
    created_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbol_versions_qname
    ON symbol_versions (workspace_id, qualified_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_symbol_versions_recent
    ON symbol_versions (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_symbol_versions_signature
    ON symbol_versions (workspace_id, signature_hash);
