-- agent-memory-lite v1.4 — feedback-aware scoring substrate.
--
-- Adds a denormalised `feedback_ewma` column on ranking-eligible kinds so the
-- retrieval scoring formula can read it without a join. EWMA values are
-- recomputed by `retrieval/feedback_aggregator.py` (off by default; gated by
-- MEMORY_FEEDBACK_EWMA_ENABLED). The summary view aggregates raw feedback
-- rows for the /memory/feedback_summary endpoint and for ad-hoc inspection.
--
-- Forward-only: column defaults to 0.0 so existing rows keep neutral
-- influence on retrieval until the aggregator runs.

ALTER TABLE chunks ADD COLUMN feedback_ewma REAL NOT NULL DEFAULT 0.0;
ALTER TABLE decisions ADD COLUMN feedback_ewma REAL NOT NULL DEFAULT 0.0;
ALTER TABLE theories ADD COLUMN feedback_ewma REAL NOT NULL DEFAULT 0.0;

-- Provenance on each feedback row so the aggregator can exclude
-- agent-self-loops (where the same task that retrieved a chunk also
-- recorded its usefulness — a known gaming vector). Existing rows get
-- "agent_observed", which is treated as untrusted by the EWMA filter
-- when MEMORY_FEEDBACK_EXCLUDE_SELF_LOOP is on.
ALTER TABLE memory_usage_feedback ADD COLUMN source TEXT NOT NULL DEFAULT 'agent_observed';

CREATE VIEW IF NOT EXISTS memory_usage_feedback_summary AS
SELECT
    workspace_id,
    source_type,
    source_id,
    SUM(usefulness) AS usefulness_sum,
    AVG(usefulness) AS usefulness_avg,
    COUNT(*) AS feedback_count,
    MAX(created_at) AS last_feedback_at,
    MIN(created_at) AS first_feedback_at
FROM memory_usage_feedback
GROUP BY workspace_id, source_type, source_id;
