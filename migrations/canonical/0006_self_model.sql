-- Phase 5 of the "memory as organ" evolution: per-workspace identity.
--
-- The agent's behaviour right now is shaped by environment (CLAUDE.md +
-- pinned behaviours + active decisions). Phase 5 adds a third dimension:
-- the agent's self-model, derived from the workspace's accumulated
-- outcome-weighted decisions / behaviours / rejected theories. The brief
-- composer surfaces ``self_model.identity_text`` as the FIRST line so the
-- agent's tone, priorities, and known weak spots stay foreground.
--
-- The model is REFRESHED, not mutated incrementally. A 6-hourly sentinel
-- pass (Phase 5 ships the function; the cron registration is operator-
-- driven for now) re-computes the whole row from the current high-outcome
-- subset, so an archived decision drops out of the identity naturally.
--
-- Companion module:
--   src/agent_memory_lite/cognition/self_model.py
--
-- The ``invariants_json`` field stores the source decision ids -- not the
-- text -- so a future operator can audit "where did this self-model
-- belief come from".

CREATE TABLE IF NOT EXISTS self_model (
    workspace_id            TEXT PRIMARY KEY,
    identity_text           TEXT NOT NULL,
    invariants_json         TEXT NOT NULL DEFAULT '[]',
    uncertainties_json      TEXT NOT NULL DEFAULT '[]',
    source_decision_ids_json TEXT NOT NULL DEFAULT '[]',
    coverage_score          REAL NOT NULL DEFAULT 0.0,
    refreshed_via           TEXT NOT NULL DEFAULT 'heuristic',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
