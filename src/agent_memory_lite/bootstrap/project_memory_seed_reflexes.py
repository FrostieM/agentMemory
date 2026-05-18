"""Phase 4: bootstrap seed reflex rules.

Three baseline reflexes ship with every new workspace. They start as
``advisory`` -- the operator promotes to ``block`` only after observing
the advisory fire reliably without false positives.

1. ``edit-requires-impact-check`` -- before ``Edit`` on any source file,
   the agent must have called ``memory_impact_check`` in this session.

2. ``decision-write-requires-search`` -- before writing a new decision
   (``memory_write`` with ``kind=decision`` or the legacy
   ``memory_write_decision``), the agent must have called
   ``memory_search`` first so it sees prior decisions.

3. ``deploy-requires-playbook`` -- before any ``Bash`` matching the
   regex ``deploy|publish|release``, the agent must have called
   ``memory_invoke_skill`` for a playbook.

The seeds are idempotent on ``(workspace_id, rule_name)`` uniqueness:
re-running the bootstrap leaves existing tunable rules untouched.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

_SEED_RULES = [
    {
        "rule_name": "edit-requires-impact-check",
        "trigger_tool": "Edit",
        "trigger_pattern": r"\.(py|ts|tsx|js|jsx|go|rs|java)$",
        "precondition_kind": "impact_check_within_seconds",
        "precondition_param_json": '{"window_seconds": 60}',
        "enforcement": "advisory",
    },
    {
        "rule_name": "decision-write-requires-search",
        "trigger_tool": "memory_write",
        "trigger_pattern": r"decision",
        "precondition_kind": "memory_search_within_seconds",
        "precondition_param_json": '{"window_seconds": 60}',
        "enforcement": "advisory",
    },
    {
        "rule_name": "deploy-requires-playbook",
        "trigger_tool": "Bash",
        "trigger_pattern": r"deploy|publish|release",
        "precondition_kind": "playbook_fetch",
        "precondition_param_json": "{}",
        "enforcement": "advisory",
    },
]


def seed_reflex_rules(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    """Seed baseline reflex rules. Returns the number of rows actually inserted.

    Idempotent: rows with the same ``(workspace_id, rule_name)`` are skipped.
    Failure-soft on pre-migration DBs (returns 0 silently).
    """
    inserted = 0
    now = iso_now()
    for spec in _SEED_RULES:
        try:
            row = conn.execute(
                "SELECT 1 FROM reflex_rules WHERE workspace_id = ? AND rule_name = ?",
                (workspace_id, spec["rule_name"]),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        if row is not None:
            continue
        try:
            conn.execute(
                """INSERT INTO reflex_rules
                   (id, workspace_id, rule_name, trigger_tool, trigger_pattern,
                    precondition_kind, precondition_param_json, enforcement,
                    active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    new_id(IdKind.AUDIT),
                    workspace_id,
                    spec["rule_name"],
                    spec["trigger_tool"],
                    spec["trigger_pattern"],
                    spec["precondition_kind"],
                    spec["precondition_param_json"],
                    spec["enforcement"],
                    now,
                    now,
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            # Concurrent seed by another process; just skip.
            continue
    conn.commit()
    return inserted
