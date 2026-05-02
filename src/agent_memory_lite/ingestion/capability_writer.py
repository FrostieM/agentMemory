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


def upsert_agent_role(conn: sqlite3.Connection, payload: AgentRoleIn) -> AgentRole:
    role_id = new_id(IdKind.AGENT_ROLE)
    timestamp = iso_now()
    with with_tx(conn):
        upsert_role_row(
            conn,
            role_id=role_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            purpose=payload.purpose,
            responsibilities=payload.responsibilities,
            boundaries=payload.boundaries,
            handoff_triggers=payload.handoff_triggers,
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
    with with_tx(conn):
        upsert_skill_row(
            conn,
            skill_id=skill_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            summary=payload.summary,
            when_to_use=payload.when_to_use,
            inputs=payload.inputs,
            outputs=payload.outputs,
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
    with with_tx(conn):
        upsert_playbook_row(
            conn,
            playbook_id=playbook_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            goal=payload.goal,
            triggers=payload.triggers,
            steps=payload.steps,
            success_criteria=payload.success_criteria,
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
