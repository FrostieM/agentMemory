"""Writers for reviewable memory candidates."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.ingestion.core_memory_writer import write_core_memory
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.procedural_writer import write_procedural_rule
from agent_memory_lite.models.candidates import MemoryCandidate, StoredMemoryCandidate
from agent_memory_lite.models.core_memory import CoreMemoryIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import MemoryCandidateKind, MemoryCandidateStatus
from agent_memory_lite.models.procedural import ProceduralRuleIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.candidates_repo import (
    get_candidate,
    insert_candidate_row,
    update_candidate_status,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_memory_candidate(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    candidate: MemoryCandidate,
) -> StoredMemoryCandidate:
    candidate_id = new_id(IdKind.MEMORY_CANDIDATE)
    timestamp = iso_now()
    with with_tx(conn):
        insert_candidate_row(
            conn,
            candidate_id=candidate_id,
            workspace_id=workspace_id,
            candidate=candidate,
            status=MemoryCandidateStatus.NEW,
            created_at=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="write_memory_candidate",
            target_type="memory_candidate",
            target_id=candidate_id,
            source_episode_id=candidate.source_episode_id,
            after={
                "kind": candidate.kind.value,
                "subject": candidate.subject,
                "status": MemoryCandidateStatus.NEW.value,
            },
        )
    stored = get_candidate(conn, candidate_id)
    assert stored is not None
    return stored


def reject_memory_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
) -> StoredMemoryCandidate:
    candidate = get_candidate(conn, candidate_id)
    if candidate is None:
        raise NotFoundError(f"memory_candidate {candidate_id!r} not found")
    timestamp = iso_now()
    with with_tx(conn):
        update_candidate_status(
            conn,
            candidate_id=candidate_id,
            status=MemoryCandidateStatus.REJECTED,
            updated_at=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=candidate.workspace_id,
            action="reject_memory_candidate",
            target_type="memory_candidate",
            target_id=candidate_id,
            source_episode_id=candidate.source_episode_id,
            after={"status": MemoryCandidateStatus.REJECTED.value},
        )
    updated = get_candidate(conn, candidate_id)
    assert updated is not None
    return updated


def promote_memory_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
) -> StoredMemoryCandidate:
    candidate = get_candidate(conn, candidate_id)
    if candidate is None:
        raise NotFoundError(f"memory_candidate {candidate_id!r} not found")
    if candidate.status not in {MemoryCandidateStatus.NEW, MemoryCandidateStatus.ACCEPTED}:
        raise ValidationError("only new or accepted memory candidates can be promoted")

    if candidate.kind == MemoryCandidateKind.DECISION:
        decision = write_decision(
            conn,
            DecisionIn(
                workspace_id=candidate.workspace_id,
                title=candidate.subject[:80] or "Promoted memory candidate",
                decision_text=candidate.evidence or candidate.subject,
                source_episode_id=candidate.source_episode_id,
                confidence=candidate.confidence,
                importance=candidate.importance,
            ),
        )
        target_type = "decision"
        target_id = decision.id
    elif candidate.kind == MemoryCandidateKind.PROCEDURAL_RULE:
        rule = write_procedural_rule(
            conn,
            ProceduralRuleIn(
                workspace_id=candidate.workspace_id,
                rule_text=candidate.evidence or candidate.subject,
                source_episode_id=candidate.source_episode_id,
                confidence=candidate.confidence,
                importance=candidate.importance,
            ),
        )
        target_type = "procedural_rule"
        target_id = rule.id
    elif candidate.kind == MemoryCandidateKind.CONSTRAINT:
        core = write_core_memory(
            conn,
            CoreMemoryIn(
                workspace_id=candidate.workspace_id,
                key=candidate.subject.strip().lower()[:80] or "candidate.constraint",
                value=candidate.evidence or candidate.subject,
                source_episode_id=candidate.source_episode_id,
                confidence=candidate.confidence,
                importance=candidate.importance,
            ),
        )
        target_type = "core_memory"
        target_id = core.id
    else:
        raise ValidationError(f"candidate kind {candidate.kind.value!r} is not promotable")

    timestamp = iso_now()
    with with_tx(conn):
        update_candidate_status(
            conn,
            candidate_id=candidate_id,
            status=MemoryCandidateStatus.PROMOTED,
            updated_at=timestamp,
            promoted_target_type=target_type,
            promoted_target_id=target_id,
        )
        insert_audit(
            conn,
            workspace_id=candidate.workspace_id,
            action="promote_memory_candidate",
            target_type="memory_candidate",
            target_id=candidate_id,
            source_episode_id=candidate.source_episode_id,
            after={
                "status": MemoryCandidateStatus.PROMOTED.value,
                "promoted_target_type": target_type,
                "promoted_target_id": target_id,
            },
        )
    promoted = get_candidate(conn, candidate_id)
    assert promoted is not None
    return promoted
