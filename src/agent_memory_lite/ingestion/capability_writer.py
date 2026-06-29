"""Write agent capability memory objects."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.fts.durable_fts import sync_durable_fts
from agent_memory_lite.ingestion.capability_writer_playbook import upsert_agent_playbook
from agent_memory_lite.ingestion.capability_writer_redaction import _redact, _redact_list
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.capabilities import (
    AgentRole,
    AgentRoleIn,
    AgentSkill,
    AgentSkillIn,
)
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.capabilities_repo import (
    get_role_by_name,
    get_skill_by_name,
    upsert_role_row,
    upsert_skill_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

_log = get_logger("ingestion.capability_writer")

__all__ = [
    "_redact",
    "_redact_list",
    "upsert_agent_playbook",
    "upsert_agent_role",
    "upsert_agent_skill",
]


def upsert_agent_role(conn: sqlite3.Connection, payload: AgentRoleIn) -> AgentRole:
    role_id = new_id(IdKind.AGENT_ROLE)
    timestamp = iso_now()
    # Redact every free-text field BEFORE the row hits SQLite + FTS -- including
    # ``name`` (FTS-indexed as kind='skill'), used consistently as the upsert key.
    name_safe = _redact(payload.name) or payload.name
    purpose_safe = _redact(payload.purpose) or ""
    responsibilities_safe = _redact_list(payload.responsibilities) or []
    boundaries_safe = _redact_list(payload.boundaries) or []
    handoff_safe = _redact_list(payload.handoff_triggers) or []
    with with_tx(conn):
        upsert_role_row(
            conn,
            role_id=role_id,
            workspace_id=payload.workspace_id,
            name=name_safe,
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
        stored = get_role_by_name(conn, workspace_id=payload.workspace_id, name=name_safe)
        assert stored is not None
        # Roles live in the ``skills`` table (subtype='role') and the 0007 backfill
        # + rebuild_durable_fts index EVERY skills row as kind='skill', so a role IS
        # durable_fts content. Sync inline too: once upsert_agent_skill started
        # syncing, search_kind_fts(kind='skill') became non-empty, which suppresses
        # the reader's LIKE fallback -- silently hiding an unsynced role until the
        # next rebuild tick. Sync stored.id (upsert reuses the row on name conflict).
        if not sync_durable_fts(
            conn, kind="skill", object_id=stored.id, workspace_id=payload.workspace_id
        ):
            _log.warning(
                "durable_fts_sync_skipped_on_upsert_role",
                object_id=stored.id,
                workspace_id=payload.workspace_id,
            )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_agent_role",
            target_type="agent_role",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={"name": name_safe, "active": payload.active},
        )
    role = get_role_by_name(conn, workspace_id=payload.workspace_id, name=name_safe)
    assert role is not None
    return role


def upsert_agent_skill(conn: sqlite3.Connection, payload: AgentSkillIn) -> AgentSkill:
    skill_id = new_id(IdKind.AGENT_SKILL)
    timestamp = iso_now()
    # Redact ``name`` too (FTS-indexed as kind='skill'); used consistently as key.
    name_safe = _redact(payload.name) or payload.name
    summary_safe = _redact(payload.summary) or ""
    when_to_use_safe = _redact_list(payload.when_to_use) or []
    inputs_safe = _redact_list(payload.inputs) or []
    outputs_safe = _redact_list(payload.outputs) or []
    with with_tx(conn):
        upsert_skill_row(
            conn,
            skill_id=skill_id,
            workspace_id=payload.workspace_id,
            name=name_safe,
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
        stored = get_skill_by_name(conn, workspace_id=payload.workspace_id, name=name_safe)
        assert stored is not None
        # M1 (write-atomicity batch): the skills table (all subtypes) is indexed as
        # the durable FTS kind 'skill' (name + summary + when_to_use_short). This
        # writer bypasses write_canonical's FTS choke point, so sync HERE. Upsert
        # reuses the existing row on name conflict -> sync stored.id. (Role +
        # playbook writers sync the same way -- see upsert_agent_role /
        # upsert_agent_playbook.) Failure-soft.
        if not sync_durable_fts(
            conn, kind="skill", object_id=stored.id, workspace_id=payload.workspace_id
        ):
            _log.warning(
                "durable_fts_sync_skipped_on_upsert_skill",
                object_id=stored.id,
                workspace_id=payload.workspace_id,
            )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_agent_skill",
            target_type="agent_skill",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={"name": name_safe, "active": payload.active},
        )
    skill = get_skill_by_name(conn, workspace_id=payload.workspace_id, name=name_safe)
    assert skill is not None
    return skill
