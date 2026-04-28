"""Review and promote extracted memory candidates."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_allowed
from agent_memory_lite.api.schemas.candidates import (
    CandidateActionRequest,
    CandidateResponse,
    ListCandidatesRequest,
    ListCandidatesResponse,
)
from agent_memory_lite.ingestion.candidate_writer import (
    promote_memory_candidate,
    reject_memory_candidate,
)
from agent_memory_lite.models.candidates import StoredMemoryCandidate
from agent_memory_lite.repositories.candidates_repo import list_candidates

router = APIRouter()


def _candidate_response(candidate: StoredMemoryCandidate) -> CandidateResponse:
    return CandidateResponse(
        candidate_id=candidate.id,
        workspace_id=candidate.workspace_id,
        kind=candidate.kind,
        subject=candidate.subject,
        predicate=candidate.predicate,
        object=candidate.object,
        evidence=candidate.evidence,
        confidence=candidate.confidence,
        importance=candidate.importance,
        trust_level=candidate.trust_level,
        temporal=candidate.temporal,
        write_targets=candidate.write_targets,
        metadata=candidate.metadata,
        source_episode_id=candidate.source_episode_id,
        status=candidate.status,
        promoted_target_type=candidate.promoted_target_type,
        promoted_target_id=candidate.promoted_target_id,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        decided_at=candidate.decided_at,
    )


@router.post("/memory/list_candidates", response_model=ListCandidatesResponse)
def list_candidates_route(
    body: ListCandidatesRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListCandidatesResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    items = list_candidates(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        statuses=body.statuses,
        limit=body.limit,
    )
    return ListCandidatesResponse(candidates=[_candidate_response(item) for item in items])


@router.post("/memory/promote_candidate", response_model=CandidateResponse)
def promote_candidate_route(body: CandidateActionRequest, conn: DbDep) -> CandidateResponse:
    return _candidate_response(promote_memory_candidate(conn, candidate_id=body.candidate_id))


@router.post("/memory/reject_candidate", response_model=CandidateResponse)
def reject_candidate_route(body: CandidateActionRequest, conn: DbDep) -> CandidateResponse:
    return _candidate_response(reject_memory_candidate(conn, candidate_id=body.candidate_id))
