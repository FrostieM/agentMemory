"""1.7.0: active-edit registry endpoints.

* POST /memory/claim_edit          — agent claims a target for editing
* POST /memory/release_edit        — release a claim
* POST /memory/list_active_edits   — read all current claims

claim_edit returns 409 when another agent already has an active claim
on the target. The claiming agent gets a 200 with the claim row;
extending an existing claim by the SAME agent_id is allowed
(idempotent re-claim).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.models.active_edits import ActiveEdit, ActiveEditIn
from agent_memory_lite.repositories.active_edits_repo import (
    cleanup_expired,
    delete_claim,
    find_active_claim,
    insert_claim,
    list_active_claims,
)

router = APIRouter()


class ClaimEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    agent_id: str = Field(min_length=1, max_length=64)
    qualified_name: str | None = Field(default=None, max_length=400)
    file_path: str | None = Field(default=None, max_length=400)
    ttl_minutes: int = Field(default=30, ge=1, le=1440)
    note: str | None = Field(default=None, max_length=500)


class ReleaseEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    claim_id: str = Field(min_length=1, max_length=64)


class ListActiveEditsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    limit: int = Field(default=100, ge=1, le=500)


class ListActiveEditsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    total: int
    edits: list[ActiveEdit]


@router.post("/memory/claim_edit", response_model=ActiveEdit)
def claim_edit_route(
    payload: ClaimEditRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ActiveEdit:
    ensure_workspace_writable(payload.workspace_id, settings)
    if payload.qualified_name is None and payload.file_path is None:
        raise HTTPException(
            status_code=400,
            detail="claim_edit requires either qualified_name or file_path",
        )
    cleanup_expired(conn, workspace_id=payload.workspace_id)
    existing = find_active_claim(
        conn,
        workspace_id=payload.workspace_id,
        qualified_name=payload.qualified_name,
        file_path=payload.file_path,
    )
    if existing is not None and existing.agent_id != payload.agent_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"target is already claimed by agent {existing.agent_id!r} "
                f"until {existing.expires_at}"
            ),
        )
    if existing is not None:
        # Same agent re-claiming → drop the old row first so the new
        # ttl + note overwrites cleanly.
        delete_claim(conn, claim_id=existing.id)
    return insert_claim(
        conn,
        ActiveEditIn(
            workspace_id=payload.workspace_id,
            agent_id=payload.agent_id,
            qualified_name=payload.qualified_name,
            file_path=payload.file_path,
            ttl_minutes=payload.ttl_minutes,
            note=payload.note,
        ),
    )


@router.post("/memory/release_edit")
def release_edit_route(
    payload: ReleaseEditRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> dict[str, object]:
    ensure_workspace_writable(payload.workspace_id, settings)
    removed = delete_claim(conn, claim_id=payload.claim_id)
    return {"workspace_id": payload.workspace_id, "released": bool(removed)}


@router.post("/memory/list_active_edits", response_model=ListActiveEditsResponse)
def list_active_edits_route(
    payload: ListActiveEditsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListActiveEditsResponse:
    ensure_workspace_readable(payload.workspace_id, settings)
    cleanup_expired(conn, workspace_id=payload.workspace_id)
    edits = list_active_claims(conn, workspace_id=payload.workspace_id, limit=payload.limit)
    return ListActiveEditsResponse(workspace_id=payload.workspace_id, total=len(edits), edits=edits)
