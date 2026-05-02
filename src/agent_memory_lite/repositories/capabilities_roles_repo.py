"""SQL operations for ``agent_roles``."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.capabilities import AgentRole
from agent_memory_lite.repositories.capabilities_search_helpers import (
    contains_any,
    json_list,
    tokens_from,
)


def _row_to_role(row: sqlite3.Row) -> AgentRole:
    return AgentRole(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        purpose=row["purpose"],
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
    conn.execute(
        """
        INSERT INTO agent_roles (
            id, workspace_id, name, purpose, responsibilities_json,
            boundaries_json, handoff_triggers_json, tools_json,
            source_episode_id, confidence, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, name) DO UPDATE SET
            purpose = excluded.purpose,
            responsibilities_json = excluded.responsibilities_json,
            boundaries_json = excluded.boundaries_json,
            handoff_triggers_json = excluded.handoff_triggers_json,
            tools_json = excluded.tools_json,
            source_episode_id = excluded.source_episode_id,
            confidence = excluded.confidence,
            active = excluded.active,
            updated_at = excluded.updated_at
        """,
        (
            role_id,
            workspace_id,
            name,
            purpose,
            json.dumps(responsibilities, sort_keys=True),
            json.dumps(boundaries, sort_keys=True),
            json.dumps(handoff_triggers, sort_keys=True),
            json.dumps(tools, sort_keys=True),
            source_episode_id,
            confidence,
            1 if active else 0,
            created_at,
            updated_at,
        ),
    )


def get_role_by_name(conn: sqlite3.Connection, *, workspace_id: str, name: str) -> AgentRole | None:
    row = conn.execute(
        "SELECT * FROM agent_roles WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_role(row) if row is not None else None


def get_role_by_id(conn: sqlite3.Connection, role_id: str) -> AgentRole | None:
    row = conn.execute("SELECT * FROM agent_roles WHERE id = ?", (role_id,)).fetchone()
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


def _rank_role(role: AgentRole, tokens: list[str]) -> tuple[float, str]:
    text = _role_text(role).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    active_bonus = 0.25 if role.active else -0.25
    return token_score + role.confidence + active_bonus, role.updated_at


def list_roles(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
) -> list[AgentRole]:
    rows = conn.execute(
        "SELECT * FROM agent_roles WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    terms = tokens_from(query)
    roles = [_row_to_role(row) for row in rows]
    if not include_inactive:
        roles = [item for item in roles if item.active]
    roles = [item for item in roles if contains_any(_role_text(item), terms)]
    roles.sort(key=lambda item: _rank_role(item, terms), reverse=True)
    return roles[:limit]
