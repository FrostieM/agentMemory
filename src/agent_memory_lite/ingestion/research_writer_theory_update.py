"""Theory-side updates triggered by an experiment result.

Split out of ``research_writer_experiments.py`` so each module stays
under the SLOC ceiling. Holds the ``confidence_after_result`` rule
table and the per-result theory-update + contradiction-insight
write.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.models.enums import (
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
    TheoryStatus,
)
from agent_memory_lite.models.research import ExperimentResultIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.research_repo import insert_insight_row
from agent_memory_lite.repositories.theories_repo import (
    get_theory,
    insert_theory_evidence_row,
    update_theory_confidence_status,
)
from agent_memory_lite.utils.ids import IdKind, new_id


def confidence_after_result(
    *,
    current_confidence: float,
    current_status: TheoryStatus,
    kind: TheoryEvidenceKind,
    evidence_confidence: float,
) -> tuple[float, TheoryStatus]:
    delta = {
        TheoryEvidenceKind.SUPPORTING: 0.12,
        TheoryEvidenceKind.REFUTING: -0.18,
        TheoryEvidenceKind.MIXED: -0.05,
        TheoryEvidenceKind.NEUTRAL: 0.0,
        TheoryEvidenceKind.EXPERIMENT: 0.0,
    }[kind] * evidence_confidence
    new_confidence = min(1.0, max(0.0, current_confidence + delta))

    new_status = current_status
    if kind is TheoryEvidenceKind.REFUTING:
        if new_confidence <= 0.15 or evidence_confidence >= 0.9:
            new_status = TheoryStatus.REJECTED
        else:
            new_status = TheoryStatus.WEAKENED
    elif kind is TheoryEvidenceKind.MIXED:
        if current_status is TheoryStatus.SUPPORTED or evidence_confidence >= 0.75:
            new_status = TheoryStatus.WEAKENED
        else:
            new_status = TheoryStatus.TESTING
    elif kind is TheoryEvidenceKind.SUPPORTING:
        if new_confidence >= 0.85:
            new_status = TheoryStatus.VALIDATED
        elif new_confidence >= 0.7:
            new_status = TheoryStatus.SUPPORTED
        else:
            new_status = TheoryStatus.TESTING
    elif kind is TheoryEvidenceKind.EXPERIMENT and current_status is TheoryStatus.PROPOSED:
        new_status = TheoryStatus.TESTING
    return new_confidence, new_status


def update_theory_after_result(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    payload: ExperimentResultIn,
    timestamp: str,
    observed_at: str,
    metrics: dict[str, Any],
) -> None:
    theory = get_theory(conn, theory_id)
    assert theory is not None
    evidence_id = new_id(IdKind.THEORY_EVIDENCE)
    insert_theory_evidence_row(
        conn,
        evidence_id=evidence_id,
        workspace_id=payload.workspace_id,
        theory_id=theory.id,
        kind=payload.kind,
        summary=payload.summary,
        source_episode_id=payload.source_episode_id,
        artifact_path=payload.artifact_path,
        metrics=metrics,
        confidence=payload.confidence,
        observed_at=observed_at,
        created_at=timestamp,
    )
    new_confidence, new_status = confidence_after_result(
        current_confidence=theory.confidence,
        current_status=theory.status,
        kind=payload.kind,
        evidence_confidence=payload.confidence,
    )
    update_theory_confidence_status(
        conn,
        theory_id=theory.id,
        confidence=new_confidence,
        status=new_status,
        updated_at=timestamp,
        last_tested_at=observed_at,
    )
    insert_audit(
        conn,
        workspace_id=payload.workspace_id,
        action="update_theory_confidence",
        target_type="theory",
        target_id=theory.id,
        source_episode_id=payload.source_episode_id,
        before={"confidence": theory.confidence, "status": theory.status.value},
        after={"confidence": new_confidence, "status": new_status.value},
    )
    if (
        payload.kind in {TheoryEvidenceKind.REFUTING, TheoryEvidenceKind.MIXED}
        and payload.confidence >= 0.65
    ):
        insight_id = new_id(IdKind.RESEARCH_INSIGHT)
        insert_insight_row(
            conn,
            insight_id=insight_id,
            workspace_id=payload.workspace_id,
            insight_type=InsightType.CONTRADICTION,
            summary=(f"Experiment result weakens theory {theory.id}: {payload.summary}"),
            proposed_action="Review the theory mechanism and design a follow-up cohort split.",
            target_type="theory",
            target_id=theory.id,
            source_episode_ids=[payload.source_episode_id]
            if payload.source_episode_id is not None
            else [],
            confidence=payload.confidence,
            status=InsightStatus.NEW,
            tags=["contradiction", payload.kind.value],
            created_at=timestamp,
        )
