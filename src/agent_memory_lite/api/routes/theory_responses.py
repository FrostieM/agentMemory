"""Wire-shape converters for theory routes.

Split out of ``theories.py`` so the route file stays under the SLOC
ceiling. The helpers below copy domain models in and out of their
wire schemas; reused by ``write_theory_route``,
``add_theory_evidence_route``, and ``list_theories_route``.
"""

from __future__ import annotations

from agent_memory_lite.api.schemas.theories import (
    AddTheoryEvidenceRequest,
    TheoryEvidenceResponse,
    TheoryResponse,
    WriteTheoryRequest,
)
from agent_memory_lite.models.theories import (
    Theory,
    TheoryEvidence,
    TheoryEvidenceIn,
    TheoryIn,
)


def theory_in_from_body(body: WriteTheoryRequest, *, source_episode_id: str | None) -> TheoryIn:
    """Build the domain ``TheoryIn`` from the wire body + resolved provenance."""
    return TheoryIn(
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
        source_episode_id=source_episode_id,
        confidence=body.confidence,
        importance=body.importance,
    )


def evidence_in_from_body(body: AddTheoryEvidenceRequest) -> TheoryEvidenceIn:
    """Build the domain ``TheoryEvidenceIn`` from the wire body."""
    return TheoryEvidenceIn(
        workspace_id=body.workspace_id,
        theory_id=body.theory_id,
        kind=body.kind,
        summary=body.summary,
        source_episode_id=body.source_episode_id,
        artifact_path=body.artifact_path,
        metrics=body.metrics,
        confidence=body.confidence,
        observed_at=body.observed_at,
    )


def to_theory_response(theory: Theory) -> TheoryResponse:
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


def to_evidence_response(evidence: TheoryEvidence) -> TheoryEvidenceResponse:
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
