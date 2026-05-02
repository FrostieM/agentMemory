"""POST /memory/pin — flip the pinned flag for high-priority memory.

Pinned decisions always appear in the active context envelope
regardless of query relevance or token budget. Pass ``pinned=false``
to un-pin.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.schemas.pin import PinRequest, PinResponse
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.pin_service import pin_memory_object

router = APIRouter()


@router.post("/memory/pin", response_model=PinResponse)
def pin_route(body: PinRequest, conn: DbDep, settings: SettingsDep) -> PinResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/pin",
        operation="pin",
        label=("Pin " if body.pinned else "Unpin ") + body.kind,
        snippet=body.id,
    ) as trace:
        try:
            result = pin_memory_object(
                conn,
                workspace_id=body.workspace_id,
                kind=body.kind,
                object_id=body.id,
                pinned=body.pinned,
            )
        except ValueError as exc:
            trace.stage_done(
                "validate",
                "Pin kind not supported",
                status="error",
                counts={"kind": body.kind},
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result["found"]:
            trace.graph_delta(
                object_type=str(result["kind"]),
                object_id=str(result["id"]),
                action="pinned" if body.pinned else "unpinned",
                label=("Pinned " if body.pinned else "Unpinned ") + body.kind,
                stage="pin",
                counts={"pinned": int(bool(body.pinned))},
            )
        return PinResponse(
            kind=str(result["kind"]),
            id=str(result["id"]),
            pinned=bool(result["pinned"]),
            found=bool(result["found"]),
        )
