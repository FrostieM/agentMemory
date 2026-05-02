"""POST /memory/what_references — reverse lookup by id.

Given a target id, returns every memory row that mentions it across
decisions, theories, insights, experiments, snapshots, chunks,
episodes, behavior instructions, and capability links. Lets the
agent answer "what references this decision / theory / concept?"
in one call instead of fanning out across list endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.repositories.references_repo import find_references

router = APIRouter()


class WhatReferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    target_id: str = Field(..., min_length=1)
    limit: int = Field(default=50, ge=1, le=200)


class ReferenceHitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    kind: str
    id: str
    label: str
    column: str


class WhatReferencesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    hits: list[ReferenceHitResponse]


@router.post("/memory/what_references", response_model=WhatReferencesResponse)
def what_references_route(
    body: WhatReferencesRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> WhatReferencesResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/what_references",
        operation="what_references",
        label="Reverse lookup",
        snippet=body.target_id,
    ) as trace:
        hits = find_references(
            conn, workspace_id=body.workspace_id, target_id=body.target_id, limit=body.limit
        )
        trace.stage_done("scan", "Reverse-lookup scan complete", counts={"hits": len(hits)})
        return WhatReferencesResponse(
            target_id=body.target_id,
            hits=[
                ReferenceHitResponse(
                    table=h.table,
                    kind=h.kind,
                    id=h.id,
                    label=h.label,
                    column=h.column,
                )
                for h in hits
            ],
        )
