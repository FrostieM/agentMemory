-- 0006: mirror dst-keyed index on soft_edges + build query-planner stats.
--
-- spreading_activation (retrieval/spreading_activation.py) walks soft_edges
-- BIDIRECTIONALLY. The pre-existing idx_soft_edges_src_weight only covers the
-- src arm, so the dst arm of the UNION degraded to a full scan of all
-- soft_edges (118k rows on the largest workspace) -- measured 86ms per
-- neighbour lookup, 86-444ms per spread(). The mirror index makes the dst
-- lookup index-backed (measured ~2ms; spread() drops to <0.5ms).
CREATE INDEX IF NOT EXISTS idx_soft_edges_dst_weight
    ON soft_edges (workspace_id, dst_qualified_name, edge_kind, weight DESC);

-- NOTE: stats (sqlite_stat1) are deliberately NOT built here. A migration runs
-- on EMPTY databases too (fresh installs, the test fixtures), and ANALYZE over
-- empty tables writes "0 rows" stats that then MISLEAD the planner once data is
-- inserted -- strictly worse than no stats. ANALYZE belongs on POPULATED data:
-- maintenance/db_hygiene runs it alongside the weekly VACUUM, and an operator
-- one-shot (scripts) builds it immediately on existing large workspaces. That
-- is what takes impact_check from ~142ms to ~0.5ms on the 118k-row workspace.
