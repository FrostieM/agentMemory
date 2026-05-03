-- agent-memory-lite v1.5 — capability maturity counters.
--
-- Skills, roles, and playbooks gain lifecycle counters so the system can
-- learn which capabilities are actually used and which earn their
-- confidence over time. Counters are written by
-- ``capability/usage_tracker.py`` (single chokepoint, audited). All four
-- columns are nullable / zero-default so existing rows behave identically
-- when the v1.5 maturity flag is off.
--
-- behavior_instructions already has ``application_count`` and
-- ``last_applied_at`` from the original schema (migration 0011). v1.5
-- starts incrementing them on each rendered envelope.

ALTER TABLE agent_skills ADD COLUMN usage_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_skills ADD COLUMN success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_skills ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_skills ADD COLUMN last_invoked_at TEXT;

ALTER TABLE agent_roles ADD COLUMN usage_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_roles ADD COLUMN success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_roles ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_roles ADD COLUMN last_invoked_at TEXT;

ALTER TABLE agent_playbooks ADD COLUMN usage_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_playbooks ADD COLUMN success_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_playbooks ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_playbooks ADD COLUMN last_invoked_at TEXT;
