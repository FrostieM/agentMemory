"""Write research-lab memory objects."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.enums import (
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
    TheoryStatus,
)
from agent_memory_lite.models.research import (
    DomainConcept,
    DomainConceptIn,
    Experiment,
    ExperimentIn,
    ExperimentResult,
    ExperimentResultIn,
    MemorySnapshot,
    MemorySnapshotIn,
    ResearchInsight,
    ResearchInsightIn,
)
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.research_repo import (
    get_concept_by_name,
    get_experiment,
    get_experiment_result,
    get_insight,
    get_snapshot,
    get_snapshot_by_key,
    insert_experiment_result_row,
    insert_experiment_row,
    insert_insight_row,
    mark_experiment_completed,
    upsert_concept_row,
    upsert_snapshot_row,
)
from agent_memory_lite.repositories.theories_repo import (
    get_theory,
    insert_theory_evidence_row,
    update_theory_confidence_status,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def _validate_workspace(
    *,
    item_workspace_id: str,
    payload_workspace_id: str,
    field_name: str,
) -> None:
    if item_workspace_id != payload_workspace_id:
        raise ValidationError(f"{field_name} must belong to the same workspace")


def register_snapshot(conn: sqlite3.Connection, payload: MemorySnapshotIn) -> MemorySnapshot:
    snapshot_id = new_id(IdKind.MEMORY_SNAPSHOT)
    timestamp = iso_now()
    with with_tx(conn):
        upsert_snapshot_row(
            conn,
            snapshot_id=snapshot_id,
            workspace_id=payload.workspace_id,
            snapshot_key=payload.snapshot_key,
            title=payload.title,
            source=payload.source,
            db_path=payload.db_path,
            duckdb_path=payload.duckdb_path,
            parquet_dir=payload.parquet_dir,
            window_start=payload.window_start,
            window_end=payload.window_end,
            build_sha=payload.build_sha,
            build_branch=payload.build_branch,
            build_time=payload.build_time,
            remote_host=payload.remote_host,
            table_counts=payload.table_counts,
            total_rows=payload.total_rows,
            metadata=payload.metadata,
            source_episode_id=payload.source_episode_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_snapshot_by_key(
            conn,
            workspace_id=payload.workspace_id,
            snapshot_key=payload.snapshot_key,
        )
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="register_snapshot",
            target_type="memory_snapshot",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={
                "snapshot_key": payload.snapshot_key,
                "total_rows": payload.total_rows,
                "duckdb_path": payload.duckdb_path,
            },
        )
    snapshot = get_snapshot_by_key(
        conn,
        workspace_id=payload.workspace_id,
        snapshot_key=payload.snapshot_key,
    )
    assert snapshot is not None
    return snapshot


def write_experiment(conn: sqlite3.Connection, payload: ExperimentIn) -> Experiment:
    if payload.theory_id is not None:
        theory = get_theory(conn, payload.theory_id)
        if theory is None:
            raise NotFoundError(f"theory_id {payload.theory_id!r} not found")
        _validate_workspace(
            item_workspace_id=theory.workspace_id,
            payload_workspace_id=payload.workspace_id,
            field_name="theory_id",
        )
    if payload.snapshot_id is not None:
        snapshot = get_snapshot(conn, payload.snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"snapshot_id {payload.snapshot_id!r} not found")
        _validate_workspace(
            item_workspace_id=snapshot.workspace_id,
            payload_workspace_id=payload.workspace_id,
            field_name="snapshot_id",
        )

    experiment_id = new_id(IdKind.EXPERIMENT)
    timestamp = iso_now()
    with with_tx(conn):
        insert_experiment_row(
            conn,
            experiment_id=experiment_id,
            workspace_id=payload.workspace_id,
            theory_id=payload.theory_id,
            snapshot_id=payload.snapshot_id,
            title=payload.title,
            hypothesis=payload.hypothesis,
            cohort_definition=payload.cohort_definition,
            success_criteria=payload.success_criteria,
            command=payload.command,
            status=payload.status,
            priority=payload.priority,
            owner=payload.owner,
            due_at=payload.due_at,
            source_episode_id=payload.source_episode_id,
            metadata=payload.metadata,
            created_at=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="write_experiment",
            target_type="research_experiment",
            target_id=experiment_id,
            source_episode_id=payload.source_episode_id,
            after={
                "title": payload.title,
                "theory_id": payload.theory_id,
                "snapshot_id": payload.snapshot_id,
                "status": payload.status.value,
            },
        )
    experiment = get_experiment(conn, experiment_id)
    assert experiment is not None
    return experiment


def _confidence_after_result(
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
        new_status = TheoryStatus.SUPPORTED if new_confidence >= 0.7 else TheoryStatus.TESTING
    elif kind is TheoryEvidenceKind.EXPERIMENT and current_status is TheoryStatus.PROPOSED:
        new_status = TheoryStatus.TESTING
    return new_confidence, new_status


def _result_metrics_with_links(
    metrics: dict[str, Any],
    *,
    experiment_id: str,
    result_id: str,
) -> dict[str, Any]:
    linked = dict(metrics)
    linked["experiment_id"] = experiment_id
    linked["experiment_result_id"] = result_id
    return linked


def _resolve_result_theory(
    conn: sqlite3.Connection,
    *,
    payload: ExperimentResultIn,
    experiment: Experiment,
) -> str | None:
    if (
        payload.theory_id is not None
        and experiment.theory_id is not None
        and payload.theory_id != experiment.theory_id
    ):
        raise ValidationError("theory_id must match the experiment theory_id")
    theory_id = payload.theory_id or experiment.theory_id
    if theory_id is None:
        return None
    theory = get_theory(conn, theory_id)
    if theory is None:
        raise NotFoundError(f"theory_id {theory_id!r} not found")
    _validate_workspace(
        item_workspace_id=theory.workspace_id,
        payload_workspace_id=payload.workspace_id,
        field_name="theory_id",
    )
    return theory_id


def add_experiment_result(
    conn: sqlite3.Connection,
    payload: ExperimentResultIn,
) -> ExperimentResult:
    experiment = get_experiment(conn, payload.experiment_id)
    if experiment is None:
        raise NotFoundError(f"experiment_id {payload.experiment_id!r} not found")
    _validate_workspace(
        item_workspace_id=experiment.workspace_id,
        payload_workspace_id=payload.workspace_id,
        field_name="experiment_id",
    )
    theory_id = _resolve_result_theory(conn, payload=payload, experiment=experiment)
    theory = get_theory(conn, theory_id) if theory_id is not None else None

    result_id = new_id(IdKind.EXPERIMENT_RESULT)
    timestamp = iso_now()
    observed_at = payload.observed_at or timestamp
    metrics = _result_metrics_with_links(
        payload.metrics,
        experiment_id=payload.experiment_id,
        result_id=result_id,
    )

    with with_tx(conn):
        insert_experiment_result_row(
            conn,
            result_id=result_id,
            workspace_id=payload.workspace_id,
            experiment_id=payload.experiment_id,
            theory_id=theory_id,
            kind=payload.kind,
            summary=payload.summary,
            metrics=metrics,
            artifact_path=payload.artifact_path,
            confidence=payload.confidence,
            observed_at=observed_at,
            source_episode_id=payload.source_episode_id,
            created_at=timestamp,
        )
        mark_experiment_completed(
            conn,
            experiment_id=payload.experiment_id,
            updated_at=timestamp,
            completed_at=observed_at,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="add_experiment_result",
            target_type="experiment_result",
            target_id=result_id,
            source_episode_id=payload.source_episode_id,
            after={
                "experiment_id": payload.experiment_id,
                "theory_id": theory_id,
                "kind": payload.kind.value,
                "artifact_path": payload.artifact_path,
            },
        )

        if theory is not None:
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
            new_confidence, new_status = _confidence_after_result(
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
                    summary=(
                        f"Experiment result {result_id} weakens theory {theory.id}: "
                        f"{payload.summary}"
                    ),
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

    result = get_experiment_result(conn, result_id)
    assert result is not None
    return result


def upsert_domain_concept(conn: sqlite3.Connection, payload: DomainConceptIn) -> DomainConcept:
    concept_id = new_id(IdKind.DOMAIN_CONCEPT)
    timestamp = iso_now()
    with with_tx(conn):
        upsert_concept_row(
            conn,
            concept_id=concept_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            kind=payload.kind,
            definition=payload.definition,
            aliases=payload.aliases,
            tags=payload.tags,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            active=payload.active,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_concept_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="upsert_domain_concept",
            target_type="domain_concept",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={"name": payload.name, "kind": payload.kind.value, "active": payload.active},
        )
    concept = get_concept_by_name(conn, workspace_id=payload.workspace_id, name=payload.name)
    assert concept is not None
    return concept


def distill_insight(conn: sqlite3.Connection, payload: ResearchInsightIn) -> ResearchInsight:
    insight_id = new_id(IdKind.RESEARCH_INSIGHT)
    timestamp = iso_now()
    with with_tx(conn):
        insert_insight_row(
            conn,
            insight_id=insight_id,
            workspace_id=payload.workspace_id,
            insight_type=payload.insight_type,
            summary=payload.summary,
            proposed_action=payload.proposed_action,
            target_type=payload.target_type,
            target_id=payload.target_id,
            source_episode_ids=payload.source_episode_ids,
            confidence=payload.confidence,
            status=payload.status,
            tags=payload.tags,
            created_at=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="distill_insight",
            target_type="research_insight",
            target_id=insight_id,
            source_episode_id=payload.source_episode_ids[0] if payload.source_episode_ids else None,
            after={
                "insight_type": payload.insight_type.value,
                "target_type": payload.target_type,
                "target_id": payload.target_id,
            },
        )
    insight = get_insight(conn, insight_id)
    assert insight is not None
    return insight
