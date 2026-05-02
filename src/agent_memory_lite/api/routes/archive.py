"""POST /memory/archive — universal soft-delete dispatcher.

Sends one object to the archive across kinds. The agent doesn't have
to remember whether the soft-delete column is ``status='archived'``,
``active=0`` or ``is_archived=1`` for the given kind - this route
maps once and writes the right field.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.schemas.archive import ArchiveRequest, ArchiveResponse
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.archive_service import archive_memory_object

router = APIRouter()


@router.post("/memory/archive", response_model=ArchiveResponse)
def archive_route(
    body: ArchiveRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ArchiveResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/archive",
        operation="archive",
        label=("Archive " if body.archive else "Restore ") + body.kind,
        snippet=body.id,
    ) as trace:
        try:
            result = archive_memory_object(
                conn,
                workspace_id=body.workspace_id,
                kind=body.kind,
                object_id=body.id,
                archive=body.archive,
            )
        except ValueError as exc:
            trace.stage_done(
                "validate",
                "Archive kind not supported",
                status="error",
                counts={"kind": body.kind},
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        trace.stage_done(
            "archive",
            "Archive flag set" if result["found"] else "Archive target not found",
            counts={"kind": body.kind, "archived": int(bool(body.archive))},
        )
        if result["found"]:
            trace.graph_delta(
                object_type=str(result["kind"]),
                object_id=str(result["id"]),
                action="archived" if body.archive else "restored",
                label=("Archived " if body.archive else "Restored ") + body.kind,
                stage="archive",
                counts={"is_archived": int(bool(body.archive))},
            )
        return ArchiveResponse(
            kind=str(result["kind"]),
            id=str(result["id"]),
            archived=bool(result["archived"]),
            found=bool(result["found"]),
        )
