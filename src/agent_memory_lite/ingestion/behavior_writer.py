"""Write persistent behavior instruction memory objects."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionIn
from agent_memory_lite.redaction.redactor import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.behavior_repo import (
    get_behavior_instruction_by_name,
    upsert_behavior_instruction_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def upsert_behavior_instruction(
    conn: sqlite3.Connection,
    payload: BehaviorInstructionIn,
) -> BehaviorInstruction:
    instruction_id = new_id(IdKind.BEHAVIOR_INSTRUCTION)
    timestamp = iso_now()
    # v3.0.0-final: redaction runs on every text field before it reaches
    # SQLite (CLAUDE.md invariant: "Secrets never stored"). An untrusted
    # document or an operator dropping an API key into the rule/rationale
    # text path would otherwise persist cleartext on disk. The
    # rationale column is NOT NULL with default "", so an empty input
    # must round-trip as empty string (not None) — redact() on "" is a
    # no-op that preserves this.
    rule_safe = redact(payload.rule).text
    rationale_safe = redact(payload.rationale or "").text
    with with_tx(conn):
        upsert_behavior_instruction_row(
            conn,
            instruction_id=instruction_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            kind=payload.kind,
            scope=payload.scope,
            priority=payload.priority,
            rule=rule_safe,
            rationale=rationale_safe,
            applies_to=payload.applies_to,
            conflict_policy=payload.conflict_policy,
            source_episode_id=payload.source_episode_id,
            source_type=payload.source_type,
            source_id=payload.source_id,
            reviewed_by=payload.reviewed_by,
            reviewed_at=payload.reviewed_at,
            expires_at=payload.expires_at,
            conflict_group=payload.conflict_group,
            confidence=payload.confidence,
            active=payload.active,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_behavior_instruction_by_name(
            conn,
            workspace_id=payload.workspace_id,
            name=payload.name,
        )
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_behavior_instruction",
            target_type="behavior_instruction",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={
                "name": payload.name,
                "kind": payload.kind.value,
                "priority": payload.priority.value,
                "source_type": payload.source_type,
                "source_id": payload.source_id,
                "expires_at": payload.expires_at,
                "active": payload.active,
            },
        )
    instruction = get_behavior_instruction_by_name(
        conn,
        workspace_id=payload.workspace_id,
        name=payload.name,
    )
    assert instruction is not None
    return instruction
