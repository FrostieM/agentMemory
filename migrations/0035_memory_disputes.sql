-- v3.3 Vector 6 disputes lifecycle.
--
-- A "dispute" is one agent's structured challenge to a memory row
-- written by a different agent. The row name comes from the roadmap
-- (V6 spec block) and the lifecycle is operator-mediated:
--
--   open    -> the proposing agent has filed a claim
--   accepted -> operator agreed with the claim; the target row
--               should be archived / superseded externally
--   rejected -> operator disagreed; the target row stays as-is
--   withdrawn -> the proposing agent retracted (e.g. found their
--                evidence was stale)
--
-- We deliberately DO NOT ship auto-resolution. The roadmap calls
-- inter-agent consensus "research territory" because it requires
-- solving alignment between agents. v3.3 MVP keeps the operator in
-- the loop; reputation + auto-resolution are deferred to v4.0+.
--
-- target_kind / target_id can point at any memory object kind
-- (decision, theory, behavior, insight, ...). evidence_json carries
-- a structured list of supporting episode / decision ids — empty by
-- default, structured by convention not enforced by schema.

CREATE TABLE IF NOT EXISTS memory_disputes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    claimant_agent_id TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'open',
    resolution TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_disputes_workspace_status
    ON memory_disputes(workspace_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_disputes_target
    ON memory_disputes(workspace_id, target_kind, target_id);
