"""Routes for maintenance events.

v3.4 #6 adds the operator-side triage routes (claim / dismiss) on top
of the existing substrate routes (list / resolve). All four routes
share the same response shape so the queue UI can refresh a single
row in place after any of them.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
)
from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.api.schemas.maintenance import (
    ClaimMaintenanceEventRequest,
    DismissMaintenanceEventRequest,
    ListMaintenanceEventsRequest,
    ListMaintenanceEventsResponse,
    MaintenanceEventResponse,
    ResolveMaintenanceEventRequest,
)
from agent_memory_lite.models.enums import MaintenanceEventStatus
from agent_memory_lite.models.maintenance import MaintenanceEvent
from agent_memory_lite.repositories.maintenance_repo import (
    claim_maintenance_event,
    dismiss_maintenance_event,
    list_maintenance_events,
    resolve_maintenance_event,
)
from agent_memory_lite.utils.time import iso_now

router = APIRouter()


def _event_response(event: MaintenanceEvent) -> MaintenanceEventResponse:
    return MaintenanceEventResponse(
        event_id=event.id,
        workspace_id=event.workspace_id,
        kind=event.kind,
        severity=event.severity,
        status=event.status,
        summary=event.summary,
        details=event.details,
        source_episode_id=event.source_episode_id,
        target_type=event.target_type,
        target_id=event.target_id,
        created_at=event.created_at,
        resolved_at=event.resolved_at,
        action_status=event.action_status,
        assigned_to=event.assigned_to,
        action_notes=event.action_notes,
        claimed_at=event.claimed_at,
        dismissed_at=event.dismissed_at,
    )


@router.post("/memory/list_maintenance_events", response_model=ListMaintenanceEventsResponse)
def list_maintenance_events_route(
    body: ListMaintenanceEventsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListMaintenanceEventsResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    events = list_maintenance_events(
        conn,
        workspace_id=body.workspace_id,
        statuses=body.statuses,
        action_statuses=body.action_statuses,
        limit=body.limit,
    )
    return ListMaintenanceEventsResponse(events=[_event_response(item) for item in events])


@router.post("/memory/resolve_maintenance_event", response_model=MaintenanceEventResponse)
def resolve_maintenance_event_route(
    body: ResolveMaintenanceEventRequest,
    conn: DbDep,
) -> MaintenanceEventResponse:
    if body.status not in {MaintenanceEventStatus.RESOLVED, MaintenanceEventStatus.IGNORED}:
        raise ValidationError("maintenance event can only be resolved or ignored")
    event = resolve_maintenance_event(
        conn,
        event_id=body.event_id,
        status=body.status,
        resolved_at=iso_now(),
    )
    if event is None:
        raise NotFoundError(f"maintenance event not found: {body.event_id}")
    return _event_response(event)


@router.post("/memory/claim_maintenance_event", response_model=MaintenanceEventResponse)
def claim_maintenance_event_route(
    body: ClaimMaintenanceEventRequest,
    conn: DbDep,
) -> MaintenanceEventResponse:
    """v3.4 #6 — operator claims an event for triage. Does not touch
    substrate ``status`` because the underlying drift is still real."""
    event = claim_maintenance_event(
        conn,
        event_id=body.event_id,
        assigned_to=body.assigned_to,
        claimed_at=iso_now(),
        action_notes=body.action_notes,
    )
    if event is None:
        raise NotFoundError(f"maintenance event not found: {body.event_id}")
    return _event_response(event)


@router.post("/memory/dismiss_maintenance_event", response_model=MaintenanceEventResponse)
def dismiss_maintenance_event_route(
    body: DismissMaintenanceEventRequest,
    conn: DbDep,
) -> MaintenanceEventResponse:
    """v3.4 #6 — operator marks the ticket non-actionable. The substrate
    drift may still be real; this is purely an operator-queue decision
    so the same finding does not block the queue forever."""
    event = dismiss_maintenance_event(
        conn,
        event_id=body.event_id,
        dismissed_at=iso_now(),
        action_notes=body.action_notes,
    )
    if event is None:
        raise NotFoundError(f"maintenance event not found: {body.event_id}")
    return _event_response(event)
