-- v3.1 performance: indexes for blindspot scan + decision_neighbor cap.
--
-- Both new v3.1 scanners use ORDER BY ... LIMIT against unindexed
-- columns. On a workspace with 1000+ episodes / 200+ active decisions
-- these become full-table scans on the brief hot path.
--
-- * Blindspot detection reads ``SELECT raw_text FROM episodes WHERE
--   workspace_id = ? AND created_at >= ?`` — already served by the
--   ``idx_episodes_workspace_created`` index shipped in 0001_init.sql
--   line 600. Vector1-audit-2 H2 caught the original 0033 duplicate;
--   removed to avoid double-write cost on the ingest hot path.
-- * Decision-neighbor suggester reads ``ORDER BY updated_at DESC LIMIT 200``
--   over ``WHERE workspace_id = ? AND status = 'active'`` — needs a
--   (workspace_id, status, updated_at) composite for the
--   recently-updated-active subset.
-- * Aging-decisions reads ``ORDER BY COALESCE(valid_from, created_at) ASC``
--   over active rows — shares the existing pinned/outcome index on
--   decisions(workspace_id, status, outcome_score). The valid_from
--   ordering also benefits from the same composite as the neighbor
--   suggester.
-- * Decision-token-set cache uses ``MAX(updated_at)`` over the same
--   filter — index lookup on the new composite is index-only.
--
-- Vector1-audit-2 M4: ``decisions.valid_from`` is added by the
-- consolidated 0001_init.sql (line 101). Any DB that successfully
-- applied migrations through 0032 already has this column. The
-- ``IF NOT EXISTS`` clause is for re-running 0033 itself, not for
-- column absence. The schema_migrations table guarantees ordering.

CREATE INDEX IF NOT EXISTS idx_decisions_workspace_status_updated_at
    ON decisions (workspace_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_decisions_workspace_status_valid_from
    ON decisions (workspace_id, status, valid_from);

-- Insights filter by (workspace_id, status, confidence range) in the
-- experiment-proposal scanner — composite covers the predicate +
-- ORDER BY confidence DESC.
CREATE INDEX IF NOT EXISTS idx_research_insights_workspace_status_confidence
    ON research_insights (workspace_id, status, confidence DESC);
