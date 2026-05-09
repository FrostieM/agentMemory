-- 1.3.0: per-agent identity in audit_log so /memory/telemetry can split
-- search/write rates per agent client (Claude vs Codex vs hub MCP vs
-- background scripts). Set via the X-Memory-Agent-Id request header;
-- NULL when the caller does not identify itself (matches existing
-- pre-1.3.0 audit rows).
ALTER TABLE audit_log ADD COLUMN agent_id TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_log_agent_id_workspace
    ON audit_log (workspace_id, agent_id, created_at);
