"""POST /memory/search — Phase 1: FTS-only search across chunks."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_allowed
from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.api.schemas.search import (
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from agent_memory_lite.fts.query import search_chunks_fts

router = APIRouter()


@router.post("/memory/search", response_model=SearchResponse)
def search_route(body: SearchRequest, conn: DbDep, settings: SettingsDep) -> SearchResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    if body.mode != "fts":
        raise ValidationError(f"unsupported search mode: {body.mode!r}")
    hits = search_chunks_fts(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        limit=body.limit,
    )
    return SearchResponse(
        mode="fts",
        hits=[
            SearchHit(
                chunk_id=h.chunk_id,
                score=h.score,
                path=h.path,
                text=h.text,
                summary=h.summary,
            )
            for h in hits
        ],
    )
