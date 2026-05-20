"""Schemas for memory maintenance events.

v3.4 #6 adds the operator-side triage surface on top of the existing
substrate-status surface: ``action_status`` filter on list, plus
dedicated request bodies for claim / dismiss endpoints. The response
model gains the new operator-side columns so the queue UI does not
need a second round trip per row.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import (
    MaintenanceActionStatus,
    MaintenanceEventStatus,
    MaintenanceSeverity,
)


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
    # v3.4 #6 — operator-side triage columns. Defaults match what
    # migration 0036 backfills, so rows written before the migration
    # serialise cleanly on the wire (action_status='open', rest None).
    action_status: MaintenanceActionStatus = MaintenanceActionStatus.OPEN
    assigned_to: str | None = None
    action_notes: str | None = None
    claimed_at: str | None = None
    dismissed_at: str | None = None


class ListMaintenanceEventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    statuses: list[MaintenanceEventStatus] | None = None
    # v3.4 #6 — filter by operator-side triage state. ``None`` means
    # "any" (preserves backwards-compatible behaviour for callers that
    # don't know about the queue page).
    action_statuses: list[MaintenanceActionStatus] | None = None
    limit: int = Field(default=20, ge=1, le=200)


class ListMaintenanceEventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[MaintenanceEventResponse]


class ResolveMaintenanceEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    status: MaintenanceEventStatus = MaintenanceEventStatus.RESOLVED


class ClaimMaintenanceEventRequest(BaseModel):
    """v3.4 #6 — operator claims an event for triage."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    assigned_to: str = Field(min_length=1, max_length=128)
    action_notes: str | None = Field(default=None, max_length=2000)


class DismissMaintenanceEventRequest(BaseModel):
    """v3.4 #6 — operator marks the ticket non-actionable. Distinct from
    resolve: the substrate-level drift may still be real, but the
    operator decided this particular ticket is not worth chasing."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    action_notes: str | None = Field(default=None, max_length=2000)
