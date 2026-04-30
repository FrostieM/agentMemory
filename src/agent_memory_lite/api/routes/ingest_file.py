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
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
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
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/ingest_file",
        operation="ingest_file",
        label="Ingest file",
        snippet=body.path,
    ) as trace:
        trace.stage_done(
            "read",
            "File payload accepted",
            counts={"chars": len(body.content), "language": body.language or ""},
            snippet=body.path,
        )
        trace.stage_started("chunk", "Chunk file content")
        result = ingest_file(
            conn,
            workspace_id=body.workspace_id,
            path=body.path,
            content=body.content,
            language=body.language,
            embedding_provider=provider,
            vector_store=store,
        )
        trace.stage_done(
            "chunk",
            "File chunks prepared",
            counts={"chunks_written": result.chunks_written, "skipped": result.skipped},
        )
        trace.stage_done("fts", "File chunks indexed", counts={"chunks": result.chunks_written})
        trace.stage_done(
            "vector",
            "Semantic vectors stored" if result.chunks_written else "No semantic vectors changed",
            status="ok" if not result.skipped else "warning",
            counts={"chunks": result.chunks_written},
        )
        trace.graph_delta(
            object_type="file",
            object_id=result.file.id,
            action="updated" if result.skipped else "indexed",
            label="File indexed" if result.chunks_written else "File unchanged",
            counts={"path": result.file.path, "chunks_written": result.chunks_written},
        )
        response = IngestFileResponse(
            file_id=result.file.id,
            path=result.file.path,
            chunks_written=result.chunks_written,
            skipped=result.skipped,
            last_indexed_at=result.file.last_indexed_at,
        )
        trace.stage_done(
            "response", "File ingest response ready", counts={"skipped": result.skipped}
        )
        return response
