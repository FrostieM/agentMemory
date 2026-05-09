-- 1.7.0: multi-agent coordination layer.
--
-- Two related tables:
--
-- 1. active_edits — short-lived claims that one agent is currently
--    editing a specific symbol or file. Other agents query this
--    before starting work to avoid clobbering. TTL-bounded (default
--    30 min) so a crashed agent doesn't lock a symbol forever.
--
-- 2. soft_edges — accumulated co-change / co-reference signals
--    between symbols. The hard graph (symbol_edges) records EXPLICIT
--    relationships (function A calls function B); soft_edges captures
--    HEURISTIC ones ("these two symbols change together a lot", "these
--    two symbols appear in the same review batch"). Weight accumulates
--    with EWMA decay so old signals fade naturally.
--
-- Together they make the multi-agent code-coordination loop the README
-- headlines as the v2.0 deliverable: hard graph for "who calls X",
-- soft graph for "who tends to change with X", active-edits for "who
-- is touching X right now".
CREATE TABLE IF NOT EXISTS active_edits (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    qualified_name TEXT,
    file_path TEXT,
    agent_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    note TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_active_edits_qname
    ON active_edits (workspace_id, qualified_name)
    WHERE qualified_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_active_edits_path
    ON active_edits (workspace_id, file_path)
    WHERE file_path IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_active_edits_expires
    ON active_edits (expires_at);

CREATE TABLE IF NOT EXISTS soft_edges (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    src_qualified_name TEXT NOT NULL,
    dst_qualified_name TEXT NOT NULL,
    edge_kind TEXT NOT NULL,        -- 'co_changed' | 'co_referenced' | 'similar_signature'
    weight REAL NOT NULL DEFAULT 1.0,
    observation_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_soft_edges_pair
    ON soft_edges (workspace_id, src_qualified_name, dst_qualified_name, edge_kind);

CREATE INDEX IF NOT EXISTS idx_soft_edges_src_weight
    ON soft_edges (workspace_id, src_qualified_name, edge_kind, weight DESC);
