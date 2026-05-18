-- Phase 7 of the "memory as organ" evolution: causal_links + full recall.
--
-- The Hebbian soft_edges from Phase 2 capture co-occurrence -- two items
-- show up together in retrievals or episodes. They do NOT capture
-- causation. ``causal_links`` records the explicit "A caused B", "A
-- enabled B", "A invalidated B", "A derived_from B" relations distilled
-- from corrected episodes and consolidation insights.
--
-- The relation set is intentionally small (4) so the SQL stays simple
-- and the agent's understanding of "why this surfaced" stays clear:
--   caused      -- A directly produced B (episode → decision)
--   enabled     -- A made B possible (decision → capability link)
--   invalidated -- A makes B no longer valid (new decision → old decision)
--   derived_from -- B is an abstraction of A (insight → episodes)
--
-- ``recall(topic)`` walks soft_edges + capability_links + causal_links
-- with a 0.5^hops decay. ``causal_extractor`` populates this table from
-- consolidation insights + corrected episodes.
--
-- Companion modules:
--   src/agent_memory_lite/retrieval/causal_extractor.py
--   src/agent_memory_lite/retrieval/recall.py

CREATE TABLE IF NOT EXISTS causal_links (
    id                   TEXT PRIMARY KEY,
    workspace_id         TEXT NOT NULL,
    src_kind             TEXT NOT NULL,
    src_id               TEXT NOT NULL,
    dst_kind             TEXT NOT NULL,
    dst_id               TEXT NOT NULL,
    relation             TEXT NOT NULL,
    weight               REAL NOT NULL DEFAULT 1.0,
    evidence_episode_id  TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE(workspace_id, src_kind, src_id, dst_kind, dst_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_causal_src ON causal_links(workspace_id, src_kind, src_id);
CREATE INDEX IF NOT EXISTS idx_causal_dst ON causal_links(workspace_id, dst_kind, dst_id);
CREATE INDEX IF NOT EXISTS idx_causal_relation
    ON causal_links(workspace_id, relation, created_at DESC);
