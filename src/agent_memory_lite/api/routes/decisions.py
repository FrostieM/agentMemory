"""POST /memory/write_decision."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep
from agent_memory_lite.api.schemas.decisions import (
    WriteDecisionRequest,
    WriteDecisionResponse,
)
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.models.decisions import DecisionIn

router = APIRouter()


@router.post("/memory/write_decision", response_model=WriteDecisionResponse)
def write_decision_route(body: WriteDecisionRequest, conn: DbDep) -> WriteDecisionResponse:
    payload = DecisionIn(
        workspace_id=body.workspace_id,
        title=body.title,
        decision_text=body.decision_text,
        rationale=body.rationale,
        supersedes_decision_id=body.supersedes_decision_id,
        source_episode_id=body.source_episode_id,
        confidence=body.confidence,
        importance=body.importance,
    )
    decision = write_decision(conn, payload)
    return WriteDecisionResponse(
        decision_id=decision.id,
        status=decision.status.value,
        valid_from=decision.valid_from,
        superseded_decision_id=decision.supersedes_decision_id,
    )
