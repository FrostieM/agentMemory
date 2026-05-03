"""GET /memory/decisions/{decision_id}/lineage — supersedes-chain walker.

Returns the chain newest-first plus a confidence trend across the chain.
Read-only; cycles in the supersede graph are reported via the
``cycle_detected`` flag rather than crashing the request.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.decisions.lineage import (
    DEFAULT_MAX_DEPTH,
    LineageNode,
    walk_lineage,
)

router = APIRouter()


class LineageNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    confidence: float
    valid_from: str
    valid_to: str | None
    supersedes_decision_id: str | None
    created_at: str
    updated_at: str


class DecisionLineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    chain: list[LineageNodeResponse]
    cycle_detected: bool
    truncated: bool
    confidence_trend: float
    confidence_first: float | None
    confidence_latest: float | None


def _to_response_node(node: LineageNode) -> LineageNodeResponse:
    return LineageNodeResponse(
        id=node.id,
        title=node.title,
        status=node.status,
        confidence=node.confidence,
        valid_from=node.valid_from,
        valid_to=node.valid_to,
        supersedes_decision_id=node.supersedes_decision_id,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


@router.get(
    "/memory/decisions/{decision_id}/lineage",
    response_model=DecisionLineageResponse,
)
def decision_lineage_route(
    decision_id: str,
    settings: SettingsDep,
    conn: DbDep,
    workspace_id: str = Query(default="default"),
    max_depth: int = Query(default=DEFAULT_MAX_DEPTH, ge=1, le=128),
) -> DecisionLineageResponse:
    ensure_workspace_readable(workspace_id, settings)
    lineage = walk_lineage(
        conn,
        workspace_id=workspace_id,
        decision_id=decision_id,
        max_depth=max_depth,
    )
    if not lineage.chain:
        raise HTTPException(
            status_code=404,
            detail=f"decision_id {decision_id!r} not found in workspace {workspace_id!r}",
        )
    return DecisionLineageResponse(
        decision_id=lineage.decision_id,
        chain=[_to_response_node(node) for node in lineage.chain],
        cycle_detected=lineage.cycle_detected,
        truncated=lineage.truncated,
        confidence_trend=lineage.confidence_trend,
        confidence_first=lineage.confidence_first,
        confidence_latest=lineage.confidence_latest,
    )
