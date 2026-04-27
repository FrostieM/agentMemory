"""Theory memory routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep
from agent_memory_lite.api.schemas.theories import (
    AddTheoryEvidenceRequest,
    ListTheoriesRequest,
    ListTheoriesResponse,
    TheoryEvidenceResponse,
    TheoryResponse,
    TheoryWithEvidenceResponse,
    WriteTheoryRequest,
)
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
def write_theory_route(body: WriteTheoryRequest, conn: DbDep) -> TheoryResponse:
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
    return _theory_response(theory)


@router.post("/memory/add_theory_evidence", response_model=TheoryEvidenceResponse)
def add_theory_evidence_route(
    body: AddTheoryEvidenceRequest,
    conn: DbDep,
) -> TheoryEvidenceResponse:
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
    return _evidence_response(evidence)


@router.post("/memory/list_theories", response_model=ListTheoriesResponse)
def list_theories_route(body: ListTheoriesRequest, conn: DbDep) -> ListTheoriesResponse:
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
