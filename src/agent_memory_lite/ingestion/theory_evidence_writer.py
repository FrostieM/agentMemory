"""Insert a theory_evidence row + post-write bridge check (v1.7)."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.theories import TheoryEvidence, TheoryEvidenceIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.theories_repo import get_theory
from agent_memory_lite.repositories.theory_evidence_repo import (
    get_theory_evidence,
    insert_theory_evidence_row,
)
from agent_memory_lite.theories.promotion_bridge import maybe_emit_decision_candidate
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def add_theory_evidence(
    conn: sqlite3.Connection,
    payload: TheoryEvidenceIn,
    *,
    settings: Settings | None = None,
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
    # v1.7: evidence may have pushed the theory past the bridge threshold.
    maybe_emit_decision_candidate(
        conn,
        workspace_id=payload.workspace_id,
        theory_id=payload.theory_id,
        settings=settings if settings is not None else get_settings(),
    )
    return evidence
