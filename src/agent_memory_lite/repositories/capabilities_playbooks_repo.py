"""SQL operations for ``agent_playbooks``."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.capabilities import AgentPlaybook
from agent_memory_lite.repositories.capabilities_search_helpers import (
    contains_any,
    json_list,
    tokens_from,
)


def _row_to_playbook(row: sqlite3.Row) -> AgentPlaybook:
    return AgentPlaybook(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        goal=row["goal"],
        triggers=json_list(row["triggers_json"]),
        steps=json_list(row["steps_json"]),
        success_criteria=json_list(row["success_criteria_json"]),
        required_skills=json_list(row["required_skills_json"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_playbook_row(
    conn: sqlite3.Connection,
    *,
    playbook_id: str,
    workspace_id: str,
    name: str,
    goal: str,
    triggers: list[str],
    steps: list[str],
    success_criteria: list[str],
    required_skills: list[str],
    source_episode_id: str | None,
    confidence: float,
    active: bool,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_playbooks (
            id, workspace_id, name, goal, triggers_json, steps_json,
            success_criteria_json, required_skills_json, source_episode_id,
            confidence, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, name) DO UPDATE SET
            goal = excluded.goal,
            triggers_json = excluded.triggers_json,
            steps_json = excluded.steps_json,
            success_criteria_json = excluded.success_criteria_json,
            required_skills_json = excluded.required_skills_json,
            source_episode_id = excluded.source_episode_id,
            confidence = excluded.confidence,
            active = excluded.active,
            updated_at = excluded.updated_at
        """,
        (
            playbook_id,
            workspace_id,
            name,
            goal,
            json.dumps(triggers, sort_keys=True),
            json.dumps(steps, sort_keys=True),
            json.dumps(success_criteria, sort_keys=True),
            json.dumps(required_skills, sort_keys=True),
            source_episode_id,
            confidence,
            1 if active else 0,
            created_at,
            updated_at,
        ),
    )


def get_playbook_by_name(
    conn: sqlite3.Connection, *, workspace_id: str, name: str
) -> AgentPlaybook | None:
    row = conn.execute(
        "SELECT * FROM agent_playbooks WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_playbook(row) if row is not None else None


def get_playbook_by_id(conn: sqlite3.Connection, playbook_id: str) -> AgentPlaybook | None:
    row = conn.execute("SELECT * FROM agent_playbooks WHERE id = ?", (playbook_id,)).fetchone()
    return _row_to_playbook(row) if row is not None else None


def _playbook_text(playbook: AgentPlaybook) -> str:
    return " ".join(
        [
            playbook.name,
            playbook.goal,
            " ".join(playbook.triggers),
            " ".join(playbook.steps),
            " ".join(playbook.success_criteria),
            " ".join(playbook.required_skills),
        ]
    )


def _rank_playbook(playbook: AgentPlaybook, tokens: list[str]) -> tuple[float, str]:
    text = _playbook_text(playbook).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    active_bonus = 0.25 if playbook.active else -0.25
    return token_score + playbook.confidence + active_bonus, playbook.updated_at


def list_playbooks(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
) -> list[AgentPlaybook]:
    rows = conn.execute(
        "SELECT * FROM agent_playbooks WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    terms = tokens_from(query)
    playbooks = [_row_to_playbook(row) for row in rows]
    if not include_inactive:
        playbooks = [item for item in playbooks if item.active]
    playbooks = [item for item in playbooks if contains_any(_playbook_text(item), terms)]
    playbooks.sort(key=lambda item: _rank_playbook(item, terms), reverse=True)
    return playbooks[:limit]
