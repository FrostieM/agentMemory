"""Handlers for candidate review + maintenance events."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion.candidate_writer import (
    promote_memory_candidate,
    reject_memory_candidate,
)
from agent_memory_lite.mcp.stdio_guards import _workspace_from_args
from agent_memory_lite.mcp.stdio_payloads import (
    _candidate_payload,
    _maintenance_event_payload,
)
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.repositories.candidates_repo import list_candidates
from agent_memory_lite.repositories.maintenance_repo import (
    list_maintenance_events,
    resolve_maintenance_event,
)
from agent_memory_lite.utils.time import iso_now


def _handle_list_candidates(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MemoryCandidateStatus  # noqa: PLC0415

    workspace_id = _workspace_from_args(args, intent="read")
    raw_statuses = args.get("statuses")
    statuses = [MemoryCandidateStatus(item) for item in raw_statuses] if raw_statuses else None
    return {
        "candidates": [
            _candidate_payload(candidate)
            for candidate in list_candidates(
                _runtime.db_for(workspace_id),
                workspace_id=workspace_id,
                query=args.get("query"),
                statuses=statuses,
                limit=int(args.get("limit", 20)),
                since=args.get("since"),
                until=args.get("until"),
            )
        ]
    }


def _handle_promote_candidate(args: dict[str, Any]) -> dict[str, Any]:
    return _candidate_payload(
        promote_memory_candidate(_runtime.db(), candidate_id=str(args["candidate_id"]))
    )


def _handle_reject_candidate(args: dict[str, Any]) -> dict[str, Any]:
    return _candidate_payload(
        reject_memory_candidate(_runtime.db(), candidate_id=str(args["candidate_id"]))
    )


def _handle_list_maintenance_events(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MaintenanceEventStatus  # noqa: PLC0415

    workspace_id = _workspace_from_args(args, intent="read")
    raw_statuses = args.get("statuses")
    statuses = [MaintenanceEventStatus(item) for item in raw_statuses] if raw_statuses else None
    return {
        "events": [
            _maintenance_event_payload(event)
            for event in list_maintenance_events(
                _runtime.db_for(workspace_id),
                workspace_id=workspace_id,
                statuses=statuses,
                limit=int(args.get("limit", 20)),
            )
        ]
    }


def _handle_resolve_maintenance_event(args: dict[str, Any]) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MaintenanceEventStatus  # noqa: PLC0415

    status = MaintenanceEventStatus(args.get("status", "resolved"))
    event = resolve_maintenance_event(
        _runtime.db(),
        event_id=str(args["event_id"]),
        status=status,
        resolved_at=iso_now(),
    )
    if event is None:
        raise ValueError(f"maintenance event not found: {args['event_id']}")
    return _maintenance_event_payload(event)
