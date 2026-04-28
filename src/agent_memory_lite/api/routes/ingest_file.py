"""POST /memory/ingest_file."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    EmbeddingProviderDep,
    SettingsDep,
    VectorStoreDep,
    ensure_workspace_allowed,
)
from agent_memory_lite.api.schemas.ingest import (
    IngestFileRequest,
    IngestFileResponse,
)
from agent_memory_lite.ingestion.file_pipeline import ingest_file

router = APIRouter()


@router.post("/memory/ingest_file", response_model=IngestFileResponse)
def ingest_file_route(
    body: IngestFileRequest,
    conn: DbDep,
    provider: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> IngestFileResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    result = ingest_file(
        conn,
        workspace_id=body.workspace_id,
        path=body.path,
        content=body.content,
        language=body.language,
        embedding_provider=provider,
        vector_store=store,
    )
    return IngestFileResponse(
        file_id=result.file.id,
        path=result.file.path,
        chunks_written=result.chunks_written,
        skipped=result.skipped,
        last_indexed_at=result.file.last_indexed_at,
    )
