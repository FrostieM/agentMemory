"""Schemas for memory maintenance events."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity


class MaintenanceEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    workspace_id: str
    kind: str
    severity: MaintenanceSeverity
    status: MaintenanceEventStatus
    summary: str
    details: dict[str, Any]
    source_episode_id: str | None
    target_type: str | None
    target_id: str | None
    created_at: str
    resolved_at: str | None


class ListMaintenanceEventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    statuses: list[MaintenanceEventStatus] | None = None
    limit: int = Field(default=20, ge=1, le=200)


class ListMaintenanceEventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[MaintenanceEventResponse]


class ResolveMaintenanceEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    status: MaintenanceEventStatus = MaintenanceEventStatus.RESOLVED
