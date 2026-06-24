"""Write agent playbook capability memory objects."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.ingestion.capability_writer_redaction import _redact, _redact_list
from agent_memory_lite.models.capabilities import AgentPlaybook, AgentPlaybookIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.capabilities_repo import (
    get_playbook_by_name,
    upsert_playbook_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


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
