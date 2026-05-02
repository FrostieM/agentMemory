"""Maintenance event models.

Maintenance events are operational facts about the memory substrate itself:
retrieval-index drift, vector upsert failures, failed repair attempts, and
similar issues that should survive log rotation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity


class MaintenanceEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    kind: str = Field(min_length=1)
    severity: MaintenanceSeverity = MaintenanceSeverity.WARNING
    status: MaintenanceEventStatus = MaintenanceEventStatus.OPEN
    summary: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    source_episode_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None


class MaintenanceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
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
