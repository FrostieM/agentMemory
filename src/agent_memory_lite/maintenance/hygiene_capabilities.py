"""Active-capability loader for hygiene.

The suggestion ranker (``suggest_capability_links``) lives in
``hygiene_capability_ranker.py`` and is re-exported here for callers
that imported the original surface.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.maintenance.hygiene_models import json_text, table_exists

__all__ = [
    "CapabilityCandidate",
    "capability_type_bonus",
    "load_active_capabilities",
    "suggest_capability_links",
    "suggested_relation",
]


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    capability_type: str
    capability_id: str
    capability_name: str
    text: str
    confidence: float


def _capability_text(row: sqlite3.Row, fields: list[str]) -> str:
    parts = [str(row["name"])]
    for field_name in fields:
        raw = row[field_name]
        if field_name.endswith("_json"):
            parts.append(json_text(str(raw) if raw is not None else None))
        elif raw:
            parts.append(str(raw))
    return " ".join(parts)


_ROLE_FIELDS = [
    "summary",
    "responsibilities_json",
    "boundaries_json",
    "handoff_triggers_json",
    "tools_json",
]
_SKILL_FIELDS = [
    "summary",
    "when_to_use_json",
    "inputs_json",
    "outputs_json",
    "tools_json",
    "related_roles_json",
]
_PLAYBOOK_FIELDS = [
    "summary",
    "triggers_json",
    "steps_json",
    "success_criteria_json",
    "required_skills_json",
]


def _candidates_from(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    table: str,
    subtype: str,
    fields: list[str],
    capability_type: str,
    sql_columns: str,
) -> list[CapabilityCandidate]:
    if not table_exists(conn, table):
        return []
    rows = conn.execute(
        f"""
        SELECT {sql_columns}
        FROM {table}
        WHERE workspace_id = ? AND subtype = ? AND active = 1
        """,
        (workspace_id, subtype),
    ).fetchall()
    return [
        CapabilityCandidate(
            capability_type=capability_type,
            capability_id=str(row["id"]),
            capability_name=str(row["name"]),
            text=_capability_text(row, fields),
            confidence=float(row["confidence"]),
        )
        for row in rows
    ]


def load_active_capabilities(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[CapabilityCandidate]:
    return [
        *_candidates_from(
            conn,
            workspace_id=workspace_id,
            table="skills",
            subtype="role",
            fields=_ROLE_FIELDS,
            capability_type="role",
            sql_columns=(
                "id, name, summary, responsibilities_json, boundaries_json, "
                "handoff_triggers_json, tools_json, confidence"
            ),
        ),
        *_candidates_from(
            conn,
            workspace_id=workspace_id,
            table="skills",
            subtype="skill",
            fields=_SKILL_FIELDS,
            capability_type="skill",
            sql_columns=(
                "id, name, summary, when_to_use_json, inputs_json, outputs_json, "
                "tools_json, related_roles_json, confidence"
            ),
        ),
        *_candidates_from(
            conn,
            workspace_id=workspace_id,
            table="skills",
            subtype="playbook",
            fields=_PLAYBOOK_FIELDS,
            capability_type="playbook",
            sql_columns=(
                "id, name, summary, triggers_json, steps_json, success_criteria_json, "
                "required_skills_json, confidence"
            ),
        ),
    ]


# Re-exports for backward compatibility (the ranker now lives in a
# sibling module; old import paths that hit hygiene_capabilities still
# work).
from agent_memory_lite.maintenance.hygiene_capability_ranker import (  # noqa: E402
    capability_type_bonus,
    suggest_capability_links,
    suggested_relation,
)
