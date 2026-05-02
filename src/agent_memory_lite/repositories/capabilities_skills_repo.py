"""SQL operations for ``agent_skills``."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.capabilities import AgentSkill
from agent_memory_lite.repositories.capabilities_search_helpers import (
    contains_any,
    json_list,
    tokens_from,
)


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


def upsert_skill_row(
    conn: sqlite3.Connection,
    *,
    skill_id: str,
    workspace_id: str,
    name: str,
    summary: str,
    when_to_use: list[str],
    inputs: list[str],
    outputs: list[str],
    tools: list[str],
    related_roles: list[str],
    source_episode_id: str | None,
    confidence: float,
    active: bool,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_skills (
            id, workspace_id, name, summary, when_to_use_json, inputs_json,
            outputs_json, tools_json, related_roles_json, source_episode_id,
            confidence, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, name) DO UPDATE SET
            summary = excluded.summary,
            when_to_use_json = excluded.when_to_use_json,
            inputs_json = excluded.inputs_json,
            outputs_json = excluded.outputs_json,
            tools_json = excluded.tools_json,
            related_roles_json = excluded.related_roles_json,
            source_episode_id = excluded.source_episode_id,
            confidence = excluded.confidence,
            active = excluded.active,
            updated_at = excluded.updated_at
        """,
        (
            skill_id,
            workspace_id,
            name,
            summary,
            json.dumps(when_to_use, sort_keys=True),
            json.dumps(inputs, sort_keys=True),
            json.dumps(outputs, sort_keys=True),
            json.dumps(tools, sort_keys=True),
            json.dumps(related_roles, sort_keys=True),
            source_episode_id,
            confidence,
            1 if active else 0,
            created_at,
            updated_at,
        ),
    )


def get_skill_by_name(
    conn: sqlite3.Connection, *, workspace_id: str, name: str
) -> AgentSkill | None:
    row = conn.execute(
        "SELECT * FROM agent_skills WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_skill(row) if row is not None else None


def get_skill_by_id(conn: sqlite3.Connection, skill_id: str) -> AgentSkill | None:
    row = conn.execute("SELECT * FROM agent_skills WHERE id = ?", (skill_id,)).fetchone()
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


def _rank_skill(skill: AgentSkill, tokens: list[str]) -> tuple[float, str]:
    text = _skill_text(skill).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    active_bonus = 0.25 if skill.active else -0.25
    return token_score + skill.confidence + active_bonus, skill.updated_at


def list_skills(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
) -> list[AgentSkill]:
    rows = conn.execute(
        "SELECT * FROM agent_skills WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    terms = tokens_from(query)
    skills = [_row_to_skill(row) for row in rows]
    if not include_inactive:
        skills = [item for item in skills if item.active]
    skills = [item for item in skills if contains_any(_skill_text(item), terms)]
    skills.sort(key=lambda item: _rank_skill(item, terms), reverse=True)
    return skills[:limit]
