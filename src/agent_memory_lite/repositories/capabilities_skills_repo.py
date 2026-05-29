"""SQL operations for skill rows in canonical ``skills``."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.capabilities import AgentSkill
from agent_memory_lite.repositories.capabilities_row_helpers import (
    body_md_from_sections,
    filter_and_rank,
)
from agent_memory_lite.repositories.capabilities_search_helpers import (
    json_list,
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
    body_md = body_md_from_sections(
        name=name,
        summary=summary,
        sections=(
            ("When to use", when_to_use),
            ("Inputs", inputs),
            ("Outputs", outputs),
            ("Tools", tools),
            ("Related roles", related_roles),
        ),
    )
    conn.execute(
        """
        INSERT INTO skills (
            id, workspace_id, name, subtype, summary, when_to_use_short,
            body_md, body_token_count, when_to_use_json, inputs_json,
            outputs_json, tools_json, related_roles_json, source_episode_id,
            confidence, base_confidence, active, created_at, updated_at
        ) VALUES (?, ?, ?, 'skill', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, subtype, name) DO UPDATE SET
            summary = excluded.summary,
            when_to_use_short = excluded.when_to_use_short,
            body_md = excluded.body_md,
            body_token_count = excluded.body_token_count,
            when_to_use_json = excluded.when_to_use_json,
            inputs_json = excluded.inputs_json,
            outputs_json = excluded.outputs_json,
            tools_json = excluded.tools_json,
            related_roles_json = excluded.related_roles_json,
            source_episode_id = excluded.source_episode_id,
            confidence = excluded.confidence,
            base_confidence = excluded.confidence,
            active = excluded.active,
            updated_at = excluded.updated_at
        """,
        (
            skill_id,
            workspace_id,
            name,
            summary,
            when_to_use[0] if when_to_use else summary,
            body_md,
            len(body_md.split()),
            json.dumps(when_to_use, sort_keys=True),
            json.dumps(inputs, sort_keys=True),
            json.dumps(outputs, sort_keys=True),
            json.dumps(tools, sort_keys=True),
            json.dumps(related_roles, sort_keys=True),
            source_episode_id,
            confidence,
            confidence,  # base_confidence: anchor the maturity curve at upsert (#121)
            1 if active else 0,
            created_at,
            updated_at,
        ),
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
