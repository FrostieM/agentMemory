-- Phase 1 of the "memory as organ" evolution: every retrievable row carries
-- a derived outcome_score in [-1.0, 1.0] so brief and lint can filter / rank
-- by demonstrated success vs failure rather than treating all knowledge as
-- equally trustworthy.
--
-- Companion module: src/agent_memory_lite/cognition/outcome_recompute.py
-- Aggregates per-row signals (existing feedback_ewma + implicit feedback +
-- archive / supersede / correction) into the denormalized column. Cron
-- sweep src/agent_memory_lite/maintenance/outcome_cron.py refreshes rows
-- whose timestamps changed since the last pass.
--
-- Defaults: 0.0 (neutral). Negative range reserved for items that failed
-- review (archived, superseded, corrected). Positive range reserved for
-- items that survived feedback + maturity decay.

ALTER TABLE decisions ADD COLUMN outcome_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE theories  ADD COLUMN outcome_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE behaviors ADD COLUMN outcome_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE skills    ADD COLUMN outcome_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE insights  ADD COLUMN outcome_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE chunks    ADD COLUMN outcome_score REAL NOT NULL DEFAULT 0.0;

CREATE INDEX IF NOT EXISTS idx_decisions_outcome
    ON decisions(workspace_id, status, outcome_score DESC);
CREATE INDEX IF NOT EXISTS idx_behaviors_outcome
    ON behaviors(workspace_id, active, outcome_score DESC);
CREATE INDEX IF NOT EXISTS idx_theories_outcome
    ON theories(workspace_id, status, outcome_score DESC);
CREATE INDEX IF NOT EXISTS idx_skills_outcome
    ON skills(workspace_id, active, outcome_score DESC);
CREATE INDEX IF NOT EXISTS idx_insights_outcome
    ON insights(workspace_id, status, outcome_score DESC);
