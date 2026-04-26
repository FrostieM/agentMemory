"""Insert a new active procedural rule (and audit it)."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.procedural import ProceduralRule, ProceduralRuleIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.procedural_repo import (
    deactivate_rule,
    insert_procedural_rule_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_procedural_rule(conn: sqlite3.Connection, payload: ProceduralRuleIn) -> ProceduralRule:
    timestamp = iso_now()
    rule_id = new_id(IdKind.PROCEDURAL_RULE)
    with with_tx(conn):
        insert_procedural_rule_row(
            conn,
            rule_id=rule_id,
            workspace_id=payload.workspace_id,
            rule_text=payload.rule_text,
            scope=payload.scope,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            importance=payload.importance,
            timestamp=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="add_procedural_rule",
            target_type="procedural_rule",
            target_id=rule_id,
            source_episode_id=payload.source_episode_id,
            after={"rule_text": payload.rule_text},
        )
    return ProceduralRule(
        id=rule_id,
        workspace_id=payload.workspace_id,
        rule_text=payload.rule_text,
        scope=payload.scope,
        active=True,
        source_episode_id=payload.source_episode_id,
        confidence=payload.confidence,
        importance=payload.importance,
        created_at=timestamp,
        updated_at=timestamp,
    )


def archive_procedural_rule(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    rule_id: str,
) -> int:
    timestamp = iso_now()
    with with_tx(conn):
        rows = deactivate_rule(conn, rule_id=rule_id, timestamp=timestamp)
        if rows:
            insert_audit(
                conn,
                workspace_id=workspace_id,
                action="archive_procedural_rule",
                target_type="procedural_rule",
                target_id=rule_id,
            )
    return rows
