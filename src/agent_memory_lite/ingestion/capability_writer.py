"""Write agent capability memory objects."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.capabilities import (
    AgentPlaybook,
    AgentPlaybookIn,
    AgentRole,
    AgentRoleIn,
    AgentSkill,
    AgentSkillIn,
)
from agent_memory_lite.redaction.redactor import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.capabilities_repo import (
    get_playbook_by_name,
    get_role_by_name,
    get_skill_by_name,
    upsert_playbook_row,
    upsert_role_row,
    upsert_skill_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


# v3.5 sector-3 audit-followup: every text field on a capability gets
# the same redaction treatment as episode/decision/theory writers got
# earlier. Capabilities ride every brief / envelope via role_activation,
# so an operator pasting `purpose="Use Bearer eyJ... to deploy"` would
# have leaked the token into every future agent context.
def _redact(text: str | None) -> str | None:
    if text is None:
        return None
    return redact(text).text


def _redact_list(items: list[str] | None) -> list[str] | None:
    if not items:
        return items
    return [redact(item).text for item in items]


def upsert_agent_role(conn: sqlite3.Connection, payload: AgentRoleIn) -> AgentRole:
    role_id = new_id(IdKind.AGENT_ROLE)
    timestamp = iso_now()
    # Redact every free-text field BEFORE the row hits SQLite + FTS.
    purpose_safe = _redact(payload.purpose) or ""
    responsibilities_safe = _redact_list(payload.responsibilities) or []
    boundaries_safe = _redact_list(payload.boundaries) or []
    handoff_safe = _redact_list(payload.handoff_triggers) or []
    with with_tx(conn):
        upsert_role_row(
            conn,
            role_id=role_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            purpose=purpose_safe,
            responsibilities=responsibilities_safe,
            boundaries=boundaries_safe,
            handoff_triggers=handoff_safe,
            tools=payload.tools,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            active=payload.active,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_role_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_agent_role",
            target_type="agent_role",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={"name": payload.name, "active": payload.active},
        )
    role = get_role_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
    assert role is not None
    return role


def upsert_agent_skill(conn: sqlite3.Connection, payload: AgentSkillIn) -> AgentSkill:
    skill_id = new_id(IdKind.AGENT_SKILL)
    timestamp = iso_now()
    summary_safe = _redact(payload.summary) or ""
    when_to_use_safe = _redact_list(payload.when_to_use) or []
    inputs_safe = _redact_list(payload.inputs) or []
    outputs_safe = _redact_list(payload.outputs) or []
    with with_tx(conn):
        upsert_skill_row(
            conn,
            skill_id=skill_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            summary=summary_safe,
            when_to_use=when_to_use_safe,
            inputs=inputs_safe,
            outputs=outputs_safe,
            tools=payload.tools,
            related_roles=payload.related_roles,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            active=payload.active,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_skill_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_agent_skill",
            target_type="agent_skill",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={"name": payload.name, "active": payload.active},
        )
    skill = get_skill_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
    assert skill is not None
    return skill


def upsert_agent_playbook(
    conn: sqlite3.Connection,
    payload: AgentPlaybookIn,
) -> AgentPlaybook:
    playbook_id = new_id(IdKind.AGENT_PLAYBOOK)
    timestamp = iso_now()
    goal_safe = _redact(payload.goal) or ""
    triggers_safe = _redact_list(payload.triggers) or []
    steps_safe = _redact_list(payload.steps) or []
    success_safe = _redact_list(payload.success_criteria) or []
    with with_tx(conn):
        upsert_playbook_row(
            conn,
            playbook_id=playbook_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            goal=goal_safe,
            triggers=triggers_safe,
            steps=steps_safe,
            success_criteria=success_safe,
            required_skills=payload.required_skills,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            active=payload.active,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_playbook_by_name(
            conn,
            workspace_id=payload.workspace_id,
            name=payload.name,
        )
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_agent_playbook",
            target_type="agent_playbook",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={"name": payload.name, "active": payload.active},
        )
    playbook = get_playbook_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
    assert playbook is not None
    return playbook
