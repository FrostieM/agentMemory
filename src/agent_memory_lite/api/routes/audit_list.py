"""POST /memory/audit - read the per-item audit trail."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.repositories.audit_list_repo import list_audit_entries

router = APIRouter()


class ListAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    target_type: str | None = None
    target_id: str | None = None
    action: str | None = None
    # ISO endpoints for the audit_log.created_at column.
    since: str | None = None
    until: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class AuditItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    action: str
    target_type: str
    target_id: str
    source_episode_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: str


class ListAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[AuditItem]


@router.post("/memory/audit", response_model=ListAuditResponse)
def list_audit_route(
    body: ListAuditRequest, conn: DbDep, settings: SettingsDep
) -> ListAuditResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/audit",
        operation="audit",
        label="Audit log",
        snippet=body.target_id or body.action or "",
    ) as trace:
        rows = list_audit_entries(
            conn,
            workspace_id=body.workspace_id,
            target_type=body.target_type,
            target_id=body.target_id,
            action=body.action,
            since=body.since,
            until=body.until,
            limit=body.limit,
        )
        trace.stage_done("read", "Audit rows fetched", counts={"rows": len(rows)})
        return ListAuditResponse(
            entries=[
                AuditItem(
                    id=row.id,
                    action=row.action,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    source_episode_id=row.source_episode_id,
                    before=row.before,
                    after=row.after,
                    created_at=row.created_at,
                )
                for row in rows
            ]
        )
