-- Phase 6 of the "memory as brain" evolution: bi-temporal facts.
--
-- ``created_at`` / ``updated_at`` records WHEN WE LEARNED IT.
-- ``valid_from`` / ``valid_to``  records WHEN THE FACT WAS / IS TRUE.
--
-- Decisions already split these (existing columns). Phase 6 extends the
-- discipline to theories, concepts, behaviors, and insights so a reader
-- can ask "what did the workspace believe on 2025-03-01?" without
-- relying on archive flags or chronological reasoning over updated_at.
--
-- NULL ``valid_from`` defaults to ``created_at`` at read time (handled
-- in ``storage/bi_temporal.where_valid``). NULL ``valid_to`` means the
-- fact is open-ended (current).
--
-- The brief filter applies bi-temporal selection automatically through
-- reader.list_kind, so an operator who supersedes a decision (sets
-- valid_to=now()) immediately drops the old view from the next brief.
--
-- Companion module:
--   src/agent_memory_lite/storage/bi_temporal.py

ALTER TABLE theories  ADD COLUMN valid_from TEXT;
ALTER TABLE theories  ADD COLUMN valid_to   TEXT;
ALTER TABLE concepts  ADD COLUMN valid_from TEXT;
ALTER TABLE concepts  ADD COLUMN valid_to   TEXT;
ALTER TABLE behaviors ADD COLUMN valid_from TEXT;
ALTER TABLE behaviors ADD COLUMN valid_to   TEXT;
ALTER TABLE insights  ADD COLUMN valid_from TEXT;
ALTER TABLE insights  ADD COLUMN valid_to   TEXT;

CREATE INDEX IF NOT EXISTS idx_theories_validity   ON theories(workspace_id, valid_to);
CREATE INDEX IF NOT EXISTS idx_concepts_validity   ON concepts(workspace_id, valid_to);
CREATE INDEX IF NOT EXISTS idx_behaviors_validity  ON behaviors(workspace_id, valid_to);
CREATE INDEX IF NOT EXISTS idx_insights_validity   ON insights(workspace_id, valid_to);
