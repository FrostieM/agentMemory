"""SQL operations for skill rows in canonical ``skills``."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.capabilities import AgentSkill
from agent_memory_lite.repositories.capabilities_row_helpers import (
    filter_and_rank,
)
from agent_memory_lite.repositories.capabilities_search_helpers import (
    json_list,
)
from agent_memory_lite.repositories.capabilities_skills_repo_write import (
    upsert_skill_row,
)

__all__ = [
    "get_skill_by_id",
    "get_skill_by_name",
    "list_skills",
    "upsert_skill_row",
]


def _row_to_skill(row: sqlite3.Row) -> AgentSkill:
    return AgentSkill(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        summary=row["summary"],
        when_to_use=json_list(row["when_to_use_json"]),
        inputs=json_list(row["inputs_json"]),
        outputs=json_list(row["outputs_json"]),
        tools=json_list(row["tools_json"]),
        related_roles=json_list(row["related_roles_json"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_skill_by_name(
    conn: sqlite3.Connection, *, workspace_id: str, name: str
) -> AgentSkill | None:
    row = conn.execute(
        "SELECT * FROM skills WHERE workspace_id = ? AND subtype = 'skill' AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_skill(row) if row is not None else None


def get_skill_by_id(conn: sqlite3.Connection, skill_id: str) -> AgentSkill | None:
    row = conn.execute(
        "SELECT * FROM skills WHERE id = ? AND subtype = 'skill'",
        (skill_id,),
    ).fetchone()
    return _row_to_skill(row) if row is not None else None


def _skill_text(skill: AgentSkill) -> str:
    return " ".join(
        [
            skill.name,
            skill.summary,
            " ".join(skill.when_to_use),
            " ".join(skill.inputs),
            " ".join(skill.outputs),
            " ".join(skill.tools),
            " ".join(skill.related_roles),
        ]
    )


def list_skills(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
) -> list[AgentSkill]:
    rows = conn.execute(
        "SELECT * FROM skills WHERE workspace_id = ? AND subtype = 'skill'",
        (workspace_id,),
    ).fetchall()
    skills = [_row_to_skill(row) for row in rows]
    return filter_and_rank(
        skills,
        query=query,
        include_inactive=include_inactive,
        limit=limit,
        text_of=_skill_text,
        confidence_of=lambda item: item.confidence,
        active_of=lambda item: item.active,
        updated_at_of=lambda item: item.updated_at,
    )
