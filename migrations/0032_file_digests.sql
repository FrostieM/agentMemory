-- 1.8.0: narrative file digests.
--
-- Pre-1.8.0 the agent could enumerate a file's chunks via FTS or
-- find_symbols, but had to assemble the higher-level "what does
-- this file do?" picture in its head every time. The digest is a
-- one-paragraph + structured-fields summary built from the existing
-- chunks / symbol_edges / symbol_versions tables. It collapses the
-- file's purpose into a single retrievable record so the upcoming
-- v2.0 dashboard can render the workspace overview without
-- re-walking the substrate.
--
-- One row per (workspace_id, file_path). Rebuilt on every file
-- ingest pass — this is a derived snapshot, not a content store,
-- so re-derivation is cheap.
CREATE TABLE IF NOT EXISTS file_digests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    symbol_count INTEGER NOT NULL DEFAULT 0,
    inbound_edge_count INTEGER NOT NULL DEFAULT 0,
    outbound_edge_count INTEGER NOT NULL DEFAULT 0,
    versions_recent INTEGER NOT NULL DEFAULT 0,  -- last 7d
    narrative TEXT NOT NULL,
    structured_json TEXT NOT NULL,               -- symbols / top edges / etc
    last_indexed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_file_digests_path
    ON file_digests (workspace_id, file_path);

CREATE INDEX IF NOT EXISTS idx_file_digests_recent
    ON file_digests (workspace_id, updated_at DESC);
