"""1.8.0: file digest endpoints.

* POST /memory/file_digest         — single file's digest by path
* POST /memory/list_file_digests   — workspace overview, newest first

Pre-1.8.0 the agent had to enumerate chunks + edges + versions to
build a "what's in this file?" view from scratch. The digest is
that view, derived once at ingest time and persisted for cheap
retrieval.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.models.file_digests import FileDigest
from agent_memory_lite.repositories.file_digests_repo import (
    get_digest,
    list_digests,
)

router = APIRouter()


class FileDigestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    file_path: str = Field(min_length=1, max_length=400)


class ListFileDigestsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    limit: int = Field(default=50, ge=1, le=500)


class ListFileDigestsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    total: int
    digests: list[FileDigest]


@router.post("/memory/file_digest", response_model=FileDigest)
def file_digest_route(
    payload: FileDigestRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> FileDigest:
    ensure_workspace_readable(payload.workspace_id, settings)
    digest = get_digest(conn, workspace_id=payload.workspace_id, file_path=payload.file_path)
    if digest is None:
        raise HTTPException(
            status_code=404,
            detail=f"no digest for {payload.file_path!r} in workspace {payload.workspace_id!r}",
        )
    return digest


@router.post("/memory/list_file_digests", response_model=ListFileDigestsResponse)
def list_file_digests_route(
    payload: ListFileDigestsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListFileDigestsResponse:
    ensure_workspace_readable(payload.workspace_id, settings)
    digests = list_digests(conn, workspace_id=payload.workspace_id, limit=payload.limit)
    return ListFileDigestsResponse(
        workspace_id=payload.workspace_id,
        total=len(digests),
        digests=digests,
    )
