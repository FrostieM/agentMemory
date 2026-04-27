"""SQL operations for agent capability memory objects."""

from __future__ import annotations

import json
import re
import sqlite3

from agent_memory_lite.models.capabilities import (
    AgentCapabilities,
    AgentPlaybook,
    AgentRole,
    AgentSkill,
)

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


def _json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _tokens(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def _contains_any(text: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    lower = text.lower()
    return any(token in lower for token in tokens)


def _row_to_role(row: sqlite3.Row) -> AgentRole:
    return AgentRole(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        purpose=row["purpose"],
        responsibilities=_json_list(row["responsibilities_json"]),
        boundaries=_json_list(row["boundaries_json"]),
        handoff_triggers=_json_list(row["handoff_triggers_json"]),
        tools=_json_list(row["tools_json"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_skill(row: sqlite3.Row) -> AgentSkill:
    return AgentSkill(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        summary=row["summary"],
        when_to_use=_json_list(row["when_to_use_json"]),
        inputs=_json_list(row["inputs_json"]),
        outputs=_json_list(row["outputs_json"]),
        tools=_json_list(row["tools_json"]),
        related_roles=_json_list(row["related_roles_json"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_playbook(row: sqlite3.Row) -> AgentPlaybook:
    return AgentPlaybook(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        goal=row["goal"],
        triggers=_json_list(row["triggers_json"]),
        steps=_json_list(row["steps_json"]),
        success_criteria=_json_list(row["success_criteria_json"]),
        required_skills=_json_list(row["required_skills_json"]),
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


def get_role_by_name(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    name: str,
) -> AgentRole | None:
    row = conn.execute(
        "SELECT * FROM agent_roles WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_role(row) if row is not None else None


def get_skill_by_name(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    name: str,
) -> AgentSkill | None:
    row = conn.execute(
        "SELECT * FROM agent_skills WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_skill(row) if row is not None else None


def get_playbook_by_name(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    name: str,
) -> AgentPlaybook | None:
    row = conn.execute(
        "SELECT * FROM agent_playbooks WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_playbook(row) if row is not None else None


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


def _rank_role(role: AgentRole, tokens: list[str]) -> tuple[float, str]:
    text = _role_text(role).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    active_bonus = 0.25 if role.active else -0.25
    return token_score + role.confidence + active_bonus, role.updated_at


def _rank_skill(skill: AgentSkill, tokens: list[str]) -> tuple[float, str]:
    text = _skill_text(skill).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    active_bonus = 0.25 if skill.active else -0.25
    return token_score + skill.confidence + active_bonus, skill.updated_at


def _rank_playbook(playbook: AgentPlaybook, tokens: list[str]) -> tuple[float, str]:
    text = _playbook_text(playbook).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    active_bonus = 0.25 if playbook.active else -0.25
    return token_score + playbook.confidence + active_bonus, playbook.updated_at


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
    terms = _tokens(query)
    roles = [_row_to_role(row) for row in rows]
    if not include_inactive:
        roles = [item for item in roles if item.active]
    roles = [item for item in roles if _contains_any(_role_text(item), terms)]
    roles.sort(key=lambda item: _rank_role(item, terms), reverse=True)
    return roles[:limit]


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
    terms = _tokens(query)
    skills = [_row_to_skill(row) for row in rows]
    if not include_inactive:
        skills = [item for item in skills if item.active]
    skills = [item for item in skills if _contains_any(_skill_text(item), terms)]
    skills.sort(key=lambda item: _rank_skill(item, terms), reverse=True)
    return skills[:limit]


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
    terms = _tokens(query)
    playbooks = [_row_to_playbook(row) for row in rows]
    if not include_inactive:
        playbooks = [item for item in playbooks if item.active]
    playbooks = [item for item in playbooks if _contains_any(_playbook_text(item), terms)]
    playbooks.sort(key=lambda item: _rank_playbook(item, terms), reverse=True)
    return playbooks[:limit]


def build_agent_capabilities(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 6,
) -> AgentCapabilities:
    per_kind_limit = max(1, limit)
    return AgentCapabilities(
        roles=list_roles(
            conn,
            workspace_id=workspace_id,
            query=query,
            include_inactive=include_inactive,
            limit=per_kind_limit,
        ),
        skills=list_skills(
            conn,
            workspace_id=workspace_id,
            query=query,
            include_inactive=include_inactive,
            limit=per_kind_limit,
        ),
        playbooks=list_playbooks(
            conn,
            workspace_id=workspace_id,
            query=query,
            include_inactive=include_inactive,
            limit=per_kind_limit,
        ),
    )
