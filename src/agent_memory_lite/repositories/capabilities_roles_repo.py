"""SQL operations for role rows in canonical ``skills``."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.capabilities import AgentRole
from agent_memory_lite.repositories.capabilities_row_helpers import (
    body_md_from_sections,
    filter_and_rank,
)
from agent_memory_lite.repositories.capabilities_search_helpers import (
    json_list,
)


def _row_to_role(row: sqlite3.Row) -> AgentRole:
    return AgentRole(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        purpose=row["summary"],
        responsibilities=json_list(row["responsibilities_json"]),
        boundaries=json_list(row["boundaries_json"]),
        handoff_triggers=json_list(row["handoff_triggers_json"]),
        tools=json_list(row["tools_json"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_role_row(
    conn: sqlite3.Connection,
    *,
    role_id: str,
    workspace_id: str,
    name: str,
    purpose: str,
    responsibilities: list[str],
    boundaries: list[str],
    handoff_triggers: list[str],
    tools: list[str],
    source_episode_id: str | None,
    confidence: float,
    active: bool,
    created_at: str,
    updated_at: str,
) -> None:
    body_md = body_md_from_sections(
        name=name,
        summary=purpose,
        sections=(
            ("Responsibilities", responsibilities),
            ("Boundaries", boundaries),
            ("Handoff triggers", handoff_triggers),
            ("Tools", tools),
        ),
    )
    conn.execute(
        """
        INSERT INTO skills (
            id, workspace_id, name, subtype, summary, when_to_use_short,
            body_md, body_token_count, responsibilities_json,
            boundaries_json, handoff_triggers_json, tools_json,
            source_episode_id, confidence, base_confidence, active,
            created_at, updated_at
        ) VALUES (?, ?, ?, 'role', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, subtype, name) DO UPDATE SET
            summary = excluded.summary,
            when_to_use_short = excluded.when_to_use_short,
            body_md = excluded.body_md,
            body_token_count = excluded.body_token_count,
            responsibilities_json = excluded.responsibilities_json,
            boundaries_json = excluded.boundaries_json,
            handoff_triggers_json = excluded.handoff_triggers_json,
            tools_json = excluded.tools_json,
            source_episode_id = excluded.source_episode_id,
            confidence = excluded.confidence,
            base_confidence = excluded.confidence,
            active = excluded.active,
            updated_at = excluded.updated_at
        """,
        (
            role_id,
            workspace_id,
            name,
            purpose,
            purpose,
            body_md,
            len(body_md.split()),
            json.dumps(responsibilities, sort_keys=True),
            json.dumps(boundaries, sort_keys=True),
            json.dumps(handoff_triggers, sort_keys=True),
            json.dumps(tools, sort_keys=True),
            source_episode_id,
            confidence,
            confidence,  # base_confidence: anchor the maturity curve at upsert (#121)
            1 if active else 0,
            created_at,
            updated_at,
        ),
    )


def get_role_by_name(conn: sqlite3.Connection, *, workspace_id: str, name: str) -> AgentRole | None:
    row = conn.execute(
        "SELECT * FROM skills WHERE workspace_id = ? AND subtype = 'role' AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_role(row) if row is not None else None


def get_role_by_id(conn: sqlite3.Connection, role_id: str) -> AgentRole | None:
    row = conn.execute(
        "SELECT * FROM skills WHERE id = ? AND subtype = 'role'",
        (role_id,),
    ).fetchone()
    return _row_to_role(row) if row is not None else None


def _role_text(role: AgentRole) -> str:
    return " ".join(
        [
            role.name,
            role.purpose,
            " ".join(role.responsibilities),
            " ".join(role.boundaries),
            " ".join(role.handoff_triggers),
            " ".join(role.tools),
        ]
    )


def list_roles(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
) -> list[AgentRole]:
    rows = conn.execute(
        "SELECT * FROM skills WHERE workspace_id = ? AND subtype = 'role'",
        (workspace_id,),
    ).fetchall()
    roles = [_row_to_role(row) for row in rows]
    return filter_and_rank(
        roles,
        query=query,
        include_inactive=include_inactive,
        limit=limit,
        text_of=_role_text,
        confidence_of=lambda item: item.confidence,
        active_of=lambda item: item.active,
        updated_at_of=lambda item: item.updated_at,
    )
