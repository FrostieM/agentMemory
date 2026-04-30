-- Vector index metadata contract.
--
-- Row-count parity proves that a vector row exists for every chunk, but it
-- does not prove that the rows were produced by the current embedding model,
-- vector backend, or chunking strategy. This table records the active vector
-- namespace contract so audit can detect stale indexes after model/schema
-- changes.

CREATE TABLE IF NOT EXISTS vector_index_metadata (
    workspace_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL,
    vector_backend TEXT NOT NULL,
    chunking_strategy TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, namespace)
);

CREATE INDEX IF NOT EXISTS idx_vector_index_metadata_workspace
    ON vector_index_metadata(workspace_id, namespace);
