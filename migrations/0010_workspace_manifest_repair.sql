-- Workspace manifest repair metadata.
--
-- Audits are normally read-only, but explicit repair tooling should leave a
-- durable timestamp so future agents know when the DB was last changed by a
-- maintenance operation.

ALTER TABLE workspace_manifest ADD COLUMN last_repair_at TEXT;
