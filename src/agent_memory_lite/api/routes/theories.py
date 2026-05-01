"""Theory memory routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.schemas.theories import (
    AddTheoryEvidenceRequest,
    ListTheoriesRequest,
    ListTheoriesResponse,
    TheoryEvidenceResponse,
    TheoryResponse,
    TheoryWithEvidenceResponse,
    WriteTheoryRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.theory_writer import add_theory_evidence, write_theory
from agent_memory_lite.models.theories import Theory, TheoryEvidence, TheoryEvidenceIn, TheoryIn
from agent_memory_lite.repositories.theories_repo import (
    list_evidence_for_theory,
    list_theories,
)

router = APIRouter()


def _theory_response(theory: Theory) -> TheoryResponse:
    return TheoryResponse(
        theory_id=theory.id,
        workspace_id=theory.workspace_id,
        title=theory.title,
        domain=theory.domain,
        claim=theory.claim,
        mechanism=theory.mechanism,
        predictions=theory.predictions,
        validation_criteria=theory.validation_criteria,
        experiment_plan=theory.experiment_plan,
        dependent_decision_ids=theory.dependent_decision_ids,
        tags=theory.tags,
        status=theory.status,
        supersedes_theory_id=theory.supersedes_theory_id,
        source_episode_id=theory.source_episode_id,
        confidence=theory.confidence,
        importance=theory.importance,
        evidence_count=theory.evidence_count,
        evidence_strength=theory.evidence_strength,
        created_at=theory.created_at,
        updated_at=theory.updated_at,
        last_tested_at=theory.last_tested_at,
    )


def _evidence_response(evidence: TheoryEvidence) -> TheoryEvidenceResponse:
    return TheoryEvidenceResponse(
        evidence_id=evidence.id,
        workspace_id=evidence.workspace_id,
        theory_id=evidence.theory_id,
        kind=evidence.kind,
        summary=evidence.summary,
        source_episode_id=evidence.source_episode_id,
        artifact_path=evidence.artifact_path,
        metrics=evidence.metrics,
        confidence=evidence.confidence,
        observed_at=evidence.observed_at,
        created_at=evidence.created_at,
    )


@router.post("/memory/write_theory", response_model=TheoryResponse)
def write_theory_route(
    body: WriteTheoryRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> TheoryResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/write_theory",
        operation="write_theory",
        label="Write theory",
        snippet=body.title,
    ) as trace:
        trace.stage_done(
            "validate",
            "Theory payload accepted",
            counts={
                "validation_criteria": len(body.validation_criteria),
                "predictions": len(body.predictions),
            },
            snippet=body.title,
        )
        trace.stage_started("persist", "Persist theory")
        theory = write_theory(
            conn,
            TheoryIn(
                workspace_id=body.workspace_id,
                title=body.title,
                claim=body.claim,
                domain=body.domain,
                mechanism=body.mechanism,
                predictions=body.predictions,
                validation_criteria=body.validation_criteria,
                experiment_plan=body.experiment_plan,
                dependent_decision_ids=body.dependent_decision_ids,
                tags=body.tags,
                status=body.status,
                supersedes_theory_id=body.supersedes_theory_id,
                source_episode_id=body.source_episode_id,
                confidence=body.confidence,
                importance=body.importance,
            ),
        )
        trace.stage_done("persist", "Theory persisted", counts={"status": theory.status})
        trace.graph_delta(
            object_type="theory",
            object_id=theory.id,
            action="created",
            label="Theory written",
        )
        response = _theory_response(theory)
        trace.stage_done("response", "Theory response ready", counts={"theory_id": theory.id})
        return response


@router.post("/memory/add_theory_evidence", response_model=TheoryEvidenceResponse)
def add_theory_evidence_route(
    body: AddTheoryEvidenceRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> TheoryEvidenceResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/add_theory_evidence",
        operation="add_theory_evidence",
        label="Add theory evidence",
        snippet=body.summary,
    ) as trace:
        trace.stage_done(
            "validate",
            "Evidence payload accepted",
            counts={"kind": body.kind, "has_metrics": bool(body.metrics)},
            snippet=body.summary,
        )
        trace.stage_started("persist", "Persist theory evidence")
        evidence = add_theory_evidence(
            conn,
            TheoryEvidenceIn(
                workspace_id=body.workspace_id,
                theory_id=body.theory_id,
                kind=body.kind,
                summary=body.summary,
                source_episode_id=body.source_episode_id,
                artifact_path=body.artifact_path,
                metrics=body.metrics,
                confidence=body.confidence,
                observed_at=body.observed_at,
            ),
        )
        trace.stage_done("persist", "Theory evidence persisted", counts={"kind": evidence.kind})
        trace.graph_delta(
            object_type="theory_evidence",
            object_id=evidence.id,
            action="created",
            label="Theory evidence added",
            counts={"theory_id": evidence.theory_id},
        )
        response = _evidence_response(evidence)
        trace.stage_done(
            "response", "Theory evidence response ready", counts={"evidence_id": evidence.id}
        )
        return response


@router.post("/memory/list_theories", response_model=ListTheoriesResponse)
def list_theories_route(
    body: ListTheoriesRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListTheoriesResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    theories = list_theories(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        statuses=body.statuses,
        limit=body.limit,
        include_archived=body.include_archived,
    )
    return ListTheoriesResponse(
        theories=[
            TheoryWithEvidenceResponse(
                theory=_theory_response(theory),
                evidence=[
                    _evidence_response(evidence)
                    for evidence in (
                        list_evidence_for_theory(conn, theory.id, limit=body.evidence_limit)
                        if body.include_evidence
                        else []
                    )
                ],
            )
            for theory in theories
        ],
    )
