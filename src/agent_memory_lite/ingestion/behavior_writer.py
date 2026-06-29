"""Write persistent behavior instruction memory objects."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.fts.durable_fts import sync_durable_fts
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionIn
from agent_memory_lite.redaction.redactor import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.behavior_repo import (
    get_behavior_instruction_by_name,
    upsert_behavior_instruction_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

_log = get_logger("ingestion.behavior_writer")


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
    # ``name`` is an FTS-indexed column (durable_fts _FTS_COLUMNS['behavior']), so a
    # secret in the name would land in the search index. Redact it (matches the
    # blessed write_canonical/redact_freetext_fields path); redact() is identity on
    # a non-secret name. name is the per-workspace upsert key, so the deterministic
    # redacted marker must be used CONSISTENTLY for the insert + every by-name get.
    name_safe = redact(payload.name).text
    with with_tx(conn):
        upsert_behavior_instruction_row(
            conn,
            instruction_id=instruction_id,
            workspace_id=payload.workspace_id,
            name=name_safe,
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
            name=name_safe,
        )
        assert stored is not None
        # M1 (write-atomicity batch): behavior is a DURABLE_FTS_KIND. This is the
        # single business writer every behavior-create path funnels through
        # (write_behavior_canonical, correction promotion, candidate promotion,
        # promote_durable, project seeding), so syncing the FTS index HERE -- not
        # in each caller -- closes the whole bypass class at one choke point.
        # Upsert reuses the existing row on name conflict, so sync stored.id (not
        # the freshly generated instruction_id). Idempotent w.r.t. write_canonical's
        # generic post-sync; failure-soft + observed. Inside the with_tx so a
        # rolled-back upsert takes its index entry with it.
        if not sync_durable_fts(
            conn, kind="behavior", object_id=stored.id, workspace_id=payload.workspace_id
        ):
            _log.warning(
                "durable_fts_sync_skipped_on_upsert_behavior",
                object_id=stored.id,
                workspace_id=payload.workspace_id,
            )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_behavior_instruction",
            target_type="behavior_instruction",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={
                "name": name_safe,
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
        name=name_safe,
    )
    assert instruction is not None
    return instruction
