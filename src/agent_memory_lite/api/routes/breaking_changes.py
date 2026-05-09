"""POST /memory/breaking_changes — recent signature-changing edits.

1.6.0: surfaces every symbol whose signature_hash changed in the
last N days, paired with downstream call-site count via the hard
graph. Lets an agent answer: "I just changed paperBot.calculate's
signature — who could break?"

The endpoint joins ``symbol_versions`` (signature change detector)
with ``symbol_edges`` (downstream callers), without committing to
the heavyweight "compute every transitive break" graph traversal —
the operator can drill down with ``/memory/graph_neighbors``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.repositories.symbol_edges_repo import list_edges_to
from agent_memory_lite.repositories.symbol_versions_breaking import (
    list_recent_signature_changes,
)

router = APIRouter()


class BreakingChangesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    since_days: int = Field(default=7, ge=1, le=365)
    limit: int = Field(default=50, ge=1, le=500)
    include_callers: bool = Field(
        default=True,
        description=(
            "When true, count downstream callers for each changed symbol. "
            "Disable for cheaper response when you just want the diff list."
        ),
    )


class BreakingChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualified_name: str
    file_path: str | None
    language: str | None
    prev_signature: str
    new_signature: str
    prev_at: str
    new_at: str
    caller_count: int


class BreakingChangesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    since_days: int
    total: int
    changes: list[BreakingChange]


@router.post("/memory/breaking_changes", response_model=BreakingChangesResponse)
def breaking_changes_route(
    payload: BreakingChangesRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> BreakingChangesResponse:
    ensure_workspace_readable(payload.workspace_id, settings)
    cutoff = (datetime.now(UTC) - timedelta(days=payload.since_days)).isoformat()
    pairs = list_recent_signature_changes(
        conn,
        workspace_id=payload.workspace_id,
        since_iso=cutoff,
        limit=payload.limit,
    )
    changes: list[BreakingChange] = []
    for prev, cur in pairs:
        caller_count = 0
        if payload.include_callers:
            callers = list_edges_to(
                conn,
                workspace_id=payload.workspace_id,
                dst_qualified_name=cur.qualified_name,
                edge_types=["calls", "instantiates"],
                limit=500,
            )
            caller_count = len(callers)
        changes.append(
            BreakingChange(
                qualified_name=cur.qualified_name,
                file_path=cur.file_path,
                language=cur.language,
                prev_signature=prev.signature_text,
                new_signature=cur.signature_text,
                prev_at=prev.created_at,
                new_at=cur.created_at,
                caller_count=caller_count,
            )
        )
    return BreakingChangesResponse(
        workspace_id=payload.workspace_id,
        since_days=payload.since_days,
        total=len(changes),
        changes=changes,
    )
