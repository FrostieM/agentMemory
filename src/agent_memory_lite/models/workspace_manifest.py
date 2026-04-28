"""Workspace manifest models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class WorkspaceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    db_uuid: str
    created_at: str
    updated_at: str
    last_audit_at: str | None
    last_audit_status: str | None
    metadata: dict[str, Any]
