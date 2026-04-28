-- Workspace manifest and product-readiness audit metadata.
--
-- The service is single-tenant per SQLite file. This manifest records the
-- intended workspace for the database so startup can reject accidental
-- cross-project use instead of silently serving the wrong memory.

CREATE TABLE IF NOT EXISTS workspace_manifest (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    workspace_id TEXT NOT NULL,
    db_uuid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_audit_at TEXT,
    last_audit_status TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
