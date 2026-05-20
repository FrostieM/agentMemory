"""Write research theories. Evidence-write lives in
``theory_evidence_writer.py`` and is re-exported here for backwards
compatibility with existing import sites.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.ingestion.conflict_detect import detect_conflicts
from agent_memory_lite.ingestion.theory_evidence_writer import add_theory_evidence
from agent_memory_lite.models.theories import Theory, TheoryIn
from agent_memory_lite.redaction.redactor import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.decisions_repo import get_decision
from agent_memory_lite.repositories.theories_repo import (
    archive_theory,
    get_theory,
    insert_theory_row,
)
from agent_memory_lite.theories.promotion_bridge import maybe_emit_decision_candidate
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

__all__ = ["add_theory_evidence", "write_theory"]


def write_theory(
    conn: sqlite3.Connection,
    payload: TheoryIn,
    *,
    settings: Settings | None = None,
) -> Theory:
    theory_id = new_id(IdKind.THEORY)
    timestamp = iso_now()

    if payload.supersedes_theory_id is not None:
        prior = get_theory(conn, payload.supersedes_theory_id)
        if prior is None:
            raise NotFoundError(f"supersedes_theory_id {payload.supersedes_theory_id!r} not found")
        if prior.workspace_id != payload.workspace_id:
            raise ValidationError("supersedes_theory_id must belong to the same workspace")

    for decision_id in payload.dependent_decision_ids:
        decision = get_decision(conn, decision_id)
        if decision is None:
            raise NotFoundError(f"dependent_decision_id {decision_id!r} not found")
        if decision.workspace_id != payload.workspace_id:
            raise ValidationError("dependent_decision_ids must belong to the same workspace")

    # v3.0.0-final: redaction runs on every text field before it reaches
    # SQLite (CLAUDE.md invariant: "Secrets never stored").
    title_safe = redact(payload.title).text
    claim_safe = redact(payload.claim).text
    mechanism_safe = redact(payload.mechanism).text if payload.mechanism else None
    experiment_plan_safe = redact(payload.experiment_plan).text if payload.experiment_plan else None
    predictions_safe = [redact(p).text for p in payload.predictions]
    validation_criteria_safe = [redact(v).text for v in payload.validation_criteria]

    with with_tx(conn):
        if payload.supersedes_theory_id is not None:
            archive_theory(
                conn,
                theory_id=payload.supersedes_theory_id,
                updated_at=timestamp,
                workspace_id=payload.workspace_id,
            )
        insert_theory_row(
            conn,
            theory_id=theory_id,
            workspace_id=payload.workspace_id,
            title=title_safe,
            domain=payload.domain,
            claim=claim_safe,
            mechanism=mechanism_safe,
            predictions=predictions_safe,
            validation_criteria=validation_criteria_safe,
            experiment_plan=experiment_plan_safe,
            dependent_decision_ids=payload.dependent_decision_ids,
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
                "title": title_safe,
                "domain": payload.domain,
                "status": payload.status.value,
                "supersedes": payload.supersedes_theory_id,
                "dependent_decision_ids": payload.dependent_decision_ids,
            },
        )

    theory = get_theory(conn, theory_id)
    assert theory is not None

    effective_settings = settings if settings is not None else get_settings()
    detect_conflicts(
        conn,
        settings=effective_settings,
        workspace_id=payload.workspace_id,
        target_type="theory",
        target_id=theory_id,
        target_text=" ".join(filter(None, [title_safe, claim_safe])),
    )
    # v1.7: a theory created with status='validated' (rare but legal) gets
    # the bridge check immediately. The far more common path is post-evidence
    # — see add_theory_evidence below.
    maybe_emit_decision_candidate(
        conn,
        workspace_id=payload.workspace_id,
        theory_id=theory_id,
        settings=effective_settings,
    )
    return theory
