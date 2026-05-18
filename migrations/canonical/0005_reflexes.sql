-- Phase 4 of the "memory as organ" evolution: reflex rules.
--
-- A reflex is a hard pre-check evaluated by the PreToolUse hook BEFORE a
-- tool call executes. Where ``behaviors`` are advisory ("the agent SHOULD
-- call impact_check"), reflexes are blocking ("the agent CANNOT Edit
-- without impact_check in this session"). The hook script exits 2 on a
-- reflex violation so Claude Code refuses the call.
--
-- Each row encodes:
--   trigger_tool        which tool name fires the reflex (Edit, Bash, ...)
--   trigger_pattern     optional regex/glob to further restrict scope
--   precondition_kind   what the agent must have done first:
--                         - memory_search_within_seconds
--                         - impact_check_within_seconds
--                         - playbook_fetch
--   precondition_param_json  JSON params (e.g. window_seconds, tool_name)
--   enforcement         'advisory' (warn) or 'block' (refuse)
--   derived_from_insight_id  link back to the insight that spawned this rule
--
-- Companion modules:
--   src/agent_memory_lite/enforcement/reflex_check.py      (eval against trail)
--   src/agent_memory_lite/enforcement/reflex_distiller.py  (PreFlect-inspired)
--   src/agent_memory_lite/bootstrap/project_memory_seed_reflexes.py
--
-- Off-path: when MEMORY_REFLEX_ENABLED=false, the reflex_check module
-- short-circuits to no-violations regardless of table contents, so
-- operators can disable the whole subsystem without dropping rows.

CREATE TABLE IF NOT EXISTS reflex_rules (
    id                       TEXT PRIMARY KEY,
    workspace_id             TEXT NOT NULL,
    rule_name                TEXT NOT NULL,
    trigger_tool             TEXT NOT NULL,
    trigger_pattern          TEXT NOT NULL DEFAULT '',
    precondition_kind        TEXT NOT NULL,
    precondition_param_json  TEXT NOT NULL DEFAULT '{}',
    enforcement              TEXT NOT NULL DEFAULT 'advisory',
    derived_from_insight_id  TEXT,
    active                   INTEGER NOT NULL DEFAULT 1,
    block_count              INTEGER NOT NULL DEFAULT 0,
    advisory_count           INTEGER NOT NULL DEFAULT 0,
    last_fired_at            TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    UNIQUE(workspace_id, rule_name)
);

CREATE INDEX IF NOT EXISTS idx_reflex_rules_active
    ON reflex_rules(workspace_id, active, trigger_tool);
CREATE INDEX IF NOT EXISTS idx_reflex_rules_insight
    ON reflex_rules(workspace_id, derived_from_insight_id);
