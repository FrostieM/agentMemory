"""GET /memory/cold_candidates — read-only cold-row enumeration.

Returns rows whose ``last_retrieved_at`` is older than ``older_than_days``.
Optionally emits a single ``cold_candidate`` audit row when the v1.6
auto-queue flag is on, so a workspace running this on a schedule has a
durable signal that the scan ran.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.maintenance.cold_scanner import (
    emit_cold_candidate_events,
    find_cold_candidates,
)

router = APIRouter()


class ColdCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    id: str
    last_retrieved_at: str


class ColdCandidatesEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    older_than_days: int
    enabled: bool
    queued: bool
    by_kind: dict[str, int]
    candidates: list[ColdCandidateResponse]


@router.get("/memory/cold_candidates", response_model=ColdCandidatesEnvelope)
def cold_candidates_route(
    settings: SettingsDep,
    conn: DbDep,
    workspace_id: str = Query(...),
    older_than_days: int | None = Query(default=None, ge=1, le=3650),
    queue: bool = Query(default=False),
) -> ColdCandidatesEnvelope:
    threshold = older_than_days or settings.cold_stale_days
    ensure_workspace_readable(workspace_id, settings)
    candidates = find_cold_candidates(conn, workspace_id=workspace_id, older_than_days=threshold)
    queued = False
    if queue and settings.cold_auto_queue_enabled and candidates:
        # Auto-queue is a write — guard accordingly. Read-only enumeration
        # without queue=true never writes anything.
        ensure_workspace_writable(workspace_id, settings)
        ensure_workspace_matches_db(conn, workspace_id, settings)
        emit_cold_candidate_events(conn, workspace_id=workspace_id, candidates=candidates)
        conn.commit()
        queued = True
    by_kind: dict[str, int] = {}
    for candidate in candidates:
        by_kind[candidate.kind] = by_kind.get(candidate.kind, 0) + 1
    return ColdCandidatesEnvelope(
        workspace_id=workspace_id,
        older_than_days=threshold,
        enabled=settings.cold_tracking_enabled,
        queued=queued,
        by_kind=by_kind,
        candidates=[
            ColdCandidateResponse(kind=c.kind, id=c.id, last_retrieved_at=c.last_retrieved_at)
            for c in candidates
        ],
    )
