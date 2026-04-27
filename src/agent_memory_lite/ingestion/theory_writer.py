"""Write research theories and attach evidence."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.theories import (
    Theory,
    TheoryEvidence,
    TheoryEvidenceIn,
    TheoryIn,
)
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.theories_repo import (
    archive_theory,
    get_theory,
    get_theory_evidence,
    insert_theory_evidence_row,
    insert_theory_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_theory(conn: sqlite3.Connection, payload: TheoryIn) -> Theory:
    theory_id = new_id(IdKind.THEORY)
    timestamp = iso_now()

    if payload.supersedes_theory_id is not None:
        prior = get_theory(conn, payload.supersedes_theory_id)
        if prior is None:
            raise NotFoundError(f"supersedes_theory_id {payload.supersedes_theory_id!r} not found")
        if prior.workspace_id != payload.workspace_id:
            raise ValidationError("supersedes_theory_id must belong to the same workspace")

    with with_tx(conn):
        if payload.supersedes_theory_id is not None:
            archive_theory(
                conn,
                theory_id=payload.supersedes_theory_id,
                updated_at=timestamp,
            )
        insert_theory_row(
            conn,
            theory_id=theory_id,
            workspace_id=payload.workspace_id,
            title=payload.title,
            domain=payload.domain,
            claim=payload.claim,
            mechanism=payload.mechanism,
            predictions=payload.predictions,
            experiment_plan=payload.experiment_plan,
            tags=payload.tags,
            status=payload.status,
            supersedes_theory_id=payload.supersedes_theory_id,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            importance=payload.importance,
            created_at=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="write_theory",
            target_type="theory",
            target_id=theory_id,
            source_episode_id=payload.source_episode_id,
            after={
                "title": payload.title,
                "domain": payload.domain,
                "status": payload.status.value,
                "supersedes": payload.supersedes_theory_id,
            },
        )

    theory = get_theory(conn, theory_id)
    assert theory is not None
    return theory


def add_theory_evidence(
    conn: sqlite3.Connection,
    payload: TheoryEvidenceIn,
) -> TheoryEvidence:
    theory = get_theory(conn, payload.theory_id)
    if theory is None:
        raise NotFoundError(f"theory_id {payload.theory_id!r} not found")
    if theory.workspace_id != payload.workspace_id:
        raise ValidationError("theory_id must belong to the same workspace")

    evidence_id = new_id(IdKind.THEORY_EVIDENCE)
    timestamp = iso_now()
    observed_at = payload.observed_at or timestamp

    with with_tx(conn):
        insert_theory_evidence_row(
            conn,
            evidence_id=evidence_id,
            workspace_id=payload.workspace_id,
            theory_id=payload.theory_id,
            kind=payload.kind,
            summary=payload.summary,
            source_episode_id=payload.source_episode_id,
            artifact_path=payload.artifact_path,
            metrics=payload.metrics,
            confidence=payload.confidence,
            observed_at=observed_at,
            created_at=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="add_theory_evidence",
            target_type="theory_evidence",
            target_id=evidence_id,
            source_episode_id=payload.source_episode_id,
            after={
                "theory_id": payload.theory_id,
                "kind": payload.kind.value,
                "artifact_path": payload.artifact_path,
            },
        )

    evidence = get_theory_evidence(conn, evidence_id)
    assert evidence is not None
    return evidence
