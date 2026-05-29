"""SQL operations for playbook rows in canonical ``skills``."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.capabilities import AgentPlaybook
from agent_memory_lite.repositories.capabilities_row_helpers import (
    body_md_from_sections,
    filter_and_rank,
)
from agent_memory_lite.repositories.capabilities_search_helpers import (
    json_list,
)


def _row_to_playbook(row: sqlite3.Row) -> AgentPlaybook:
    return AgentPlaybook(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        goal=row["summary"],
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
    body_md = body_md_from_sections(
        name=name,
        summary=goal,
        sections=(
            ("Triggers", triggers),
            ("Steps", steps),
            ("Success criteria", success_criteria),
            ("Required skills", required_skills),
        ),
    )
    conn.execute(
        """
        INSERT INTO skills (
            id, workspace_id, name, subtype, summary, when_to_use_short,
            body_md, body_token_count, triggers_json, steps_json,
            success_criteria_json, required_skills_json, source_episode_id,
            confidence, base_confidence, active, created_at, updated_at
        ) VALUES (?, ?, ?, 'playbook', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, subtype, name) DO UPDATE SET
            summary = excluded.summary,
            when_to_use_short = excluded.when_to_use_short,
            body_md = excluded.body_md,
            body_token_count = excluded.body_token_count,
            triggers_json = excluded.triggers_json,
            steps_json = excluded.steps_json,
            success_criteria_json = excluded.success_criteria_json,
            required_skills_json = excluded.required_skills_json,
            source_episode_id = excluded.source_episode_id,
            confidence = excluded.confidence,
            base_confidence = excluded.confidence,
            active = excluded.active,
            updated_at = excluded.updated_at
        """,
        (
            playbook_id,
            workspace_id,
            name,
            goal,
            triggers[0] if triggers else goal,
            body_md,
            len(body_md.split()),
            json.dumps(triggers, sort_keys=True),
            json.dumps(steps, sort_keys=True),
            json.dumps(success_criteria, sort_keys=True),
            json.dumps(required_skills, sort_keys=True),
            source_episode_id,
            confidence,
            confidence,  # base_confidence: anchor the maturity curve at upsert (#121)
            1 if active else 0,
            created_at,
            updated_at,
        ),
    )


def get_playbook_by_name(
    conn: sqlite3.Connection, *, workspace_id: str, name: str
) -> AgentPlaybook | None:
    row = conn.execute(
        "SELECT * FROM skills WHERE workspace_id = ? AND subtype = 'playbook' AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_playbook(row) if row is not None else None


def get_playbook_by_id(conn: sqlite3.Connection, playbook_id: str) -> AgentPlaybook | None:
    row = conn.execute(
        "SELECT * FROM skills WHERE id = ? AND subtype = 'playbook'",
        (playbook_id,),
    ).fetchone()
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


def list_playbooks(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
) -> list[AgentPlaybook]:
    rows = conn.execute(
        "SELECT * FROM skills WHERE workspace_id = ? AND subtype = 'playbook'",
        (workspace_id,),
    ).fetchall()
    playbooks = [_row_to_playbook(row) for row in rows]
    return filter_and_rank(
        playbooks,
        query=query,
        include_inactive=include_inactive,
        limit=limit,
        text_of=_playbook_text,
        confidence_of=lambda item: item.confidence,
        active_of=lambda item: item.active,
        updated_at_of=lambda item: item.updated_at,
    )
