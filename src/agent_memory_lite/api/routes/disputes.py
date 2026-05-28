"""v3.3 Vector 6 disputes routes — propose / resolve / list.

Operator-mediated lifecycle for the inter-agent negotiation MVP. One
agent files a dispute against another agent's memory row; the
operator resolves it manually. Auto-resolution is deferred to v4.0+
because it requires research-grade alignment work the roadmap
explicitly flagged as out of scope.

Settings: ``MEMORY_DISPUTES_ENABLED`` — default True. Set to false to
disable all three routes (they return 503 Service Unavailable).
"""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.repositories.disputes_repo import (
    Dispute,
    get_dispute,
    insert_dispute,
    list_disputes,
    resolve_dispute,
)

router = APIRouter()

_TERMINAL_STATUSES = ("accepted", "rejected", "withdrawn")


def _disputes_enabled() -> bool:
    raw = os.environ.get("MEMORY_DISPUTES_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _require_enabled() -> None:
    if not _disputes_enabled():
        raise HTTPException(status_code=503, detail="memory_disputes feature disabled")


class DisputeDTO(BaseModel):
    """Wire-side projection of a dispute row."""

    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    target_kind: str
    target_id: str
    claimant_agent_id: str
    claim_text: str
    evidence: list[object] = Field(default_factory=list)
    status: str
    resolution: str = ""
    created_at: str
    resolved_at: str = ""


def _to_dto(d: Dispute) -> DisputeDTO:
    return DisputeDTO(
        id=d.id,
        workspace_id=d.workspace_id,
        target_kind=d.target_kind,
        target_id=d.target_id,
        claimant_agent_id=d.claimant_agent_id,
        claim_text=d.claim_text,
        evidence=list(d.evidence),
        status=d.status,
        resolution=d.resolution,
        created_at=d.created_at,
        resolved_at=d.resolved_at,
    )


class ProposeDisputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    target_kind: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    claimant_agent_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    evidence: list[object] = Field(default_factory=list)


class ProposeDisputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispute: DisputeDTO


class ResolveDisputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispute_id: str = Field(min_length=1)
    status: str = Field(min_length=1)  # accepted | rejected | withdrawn
    resolution: str = ""


class ResolveDisputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispute: DisputeDTO
    flipped: bool


class ListDisputesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    disputes: list[DisputeDTO]


@router.post("/memory/propose_dispute", response_model=ProposeDisputeResponse)
def propose_dispute_route(
    body: ProposeDisputeRequest, conn: DbDep, settings: SettingsDep
) -> ProposeDisputeResponse:
    """File a new dispute against a memory row. Write surface — gated
    by strict-isolation."""
    _require_enabled()
    ensure_workspace_writable(body.workspace_id, settings)
    ensure_workspace_matches_db(conn, body.workspace_id, settings)
    try:
        dispute_id = insert_dispute(
            conn,
            workspace_id=body.workspace_id,
            target_kind=body.target_kind,
            target_id=body.target_id,
            claimant_agent_id=body.claimant_agent_id,
            claim_text=body.claim_text,
            evidence=list(body.evidence),
        )
    except sqlite3.OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"memory_disputes table unavailable: {exc}",
        ) from exc
    d = get_dispute(conn, dispute_id=dispute_id)
    if d is None:  # pragma: no cover — write-then-read on same conn
        raise HTTPException(status_code=500, detail="dispute insert succeeded but read missed")
    return ProposeDisputeResponse(dispute=_to_dto(d))


@router.post("/memory/resolve_dispute", response_model=ResolveDisputeResponse)
def resolve_dispute_route(
    body: ResolveDisputeRequest, conn: DbDep, settings: SettingsDep
) -> ResolveDisputeResponse:
    """Operator (or claimant for ``withdrawn``) flips a dispute to a
    terminal state. Idempotent — re-resolving an already-terminal row
    returns flipped=False with the existing payload."""
    _require_enabled()
    if body.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {list(_TERMINAL_STATUSES)}",
        )
    current = get_dispute(conn, dispute_id=body.dispute_id)
    if current is None:
        raise HTTPException(status_code=404, detail="dispute not found")
    ensure_workspace_writable(current.workspace_id, settings)
    ensure_workspace_matches_db(conn, current.workspace_id, settings)
    flipped = resolve_dispute(
        conn,
        dispute_id=body.dispute_id,
        new_status=body.status,
        resolution=body.resolution,
    )
    updated = get_dispute(conn, dispute_id=body.dispute_id)
    if updated is None:  # pragma: no cover - same conn
        raise HTTPException(status_code=500, detail="dispute disappeared after resolve")
    return ResolveDisputeResponse(dispute=_to_dto(updated), flipped=flipped)


@router.get("/memory/disputes", response_model=ListDisputesResponse)
def list_disputes_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(min_length=1),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> ListDisputesResponse:
    """List disputes (newest first). ``status`` filter is optional."""
    _require_enabled()
    ensure_workspace_readable(workspace_id, settings)
    rows = list_disputes(conn, workspace_id=workspace_id, status=status, limit=limit)
    return ListDisputesResponse(
        workspace_id=workspace_id,
        disputes=[_to_dto(d) for d in rows],
    )
