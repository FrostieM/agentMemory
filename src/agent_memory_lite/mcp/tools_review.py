"""Candidate review + maintenance event MCP tool handlers."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion.candidate_writer import (
    promote_memory_candidate,
    reject_memory_candidate,
)
from agent_memory_lite.mcp.tools_payloads import (
    candidate_payload,
    maintenance_event_payload,
)
from agent_memory_lite.repositories.candidates_repo import list_candidates
from agent_memory_lite.repositories.maintenance_repo import (
    list_maintenance_events,
    resolve_maintenance_event,
)
from agent_memory_lite.utils.time import iso_now


def memory_list_candidates(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    statuses: list[str] | None = None,
    limit: int = 20,
    since: str | None = None,
    until: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MemoryCandidateStatus  # noqa: PLC0415

    parsed = [MemoryCandidateStatus(item) for item in statuses] if statuses else None
    return {
        "candidates": [
            candidate_payload(candidate)
            for candidate in list_candidates(
                conn,
                workspace_id=workspace_id,
                query=query,
                statuses=parsed,
                limit=limit,
                since=since,
                until=until,
            )
        ]
    }


def memory_promote_candidate(
    *, conn: sqlite3.Connection, candidate_id: str, **_kwargs: Any
) -> dict[str, Any]:
    return candidate_payload(promote_memory_candidate(conn, candidate_id=candidate_id))


def memory_promote_candidate_to_behavior(
    *,
    conn: sqlite3.Connection,
    candidate_id: str,
    name: str,
    workspace_id: str = "default",
    rule_text_override: str | None = None,
    rationale: str = "",
    kind: str = "operating_rule",
    scope: str = "workspace",
    priority: str = "user_preference",
    conflict_policy: str = "current_user_wins",
    applies_to: list[str] | str | None = None,
    decided_by: str | None = None,
    pinned: bool = False,
    overwrite: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """v1.10: promote a CORRECTION-kind candidate into a behavior_instruction."""
    from agent_memory_lite.ingestion.correction_promotion import (  # noqa: PLC0415
        CorrectionPromoteInput,
        CorrectionPromotionError,
        promote_correction_to_behavior,
    )
    from agent_memory_lite.ingestion.correction_promotion_guards import (  # noqa: PLC0415
        coerce_applies_to,
    )
    from agent_memory_lite.models.enums import (  # noqa: PLC0415
        BehaviorConflictPolicy,
        BehaviorInstructionKind,
        BehaviorInstructionPriority,
        BehaviorInstructionScope,
    )

    payload = CorrectionPromoteInput(
        workspace_id=workspace_id,
        candidate_id=candidate_id,
        name=name,
        rule_text_override=rule_text_override,
        rationale=rationale,
        kind=BehaviorInstructionKind(kind),
        scope=BehaviorInstructionScope(scope),
        priority=BehaviorInstructionPriority(priority),
        conflict_policy=BehaviorConflictPolicy(conflict_policy),
        applies_to=coerce_applies_to(applies_to),
        decided_by=decided_by,
        pinned=pinned,
        overwrite=overwrite,
    )
    try:
        instruction = promote_correction_to_behavior(conn, payload)
    except CorrectionPromotionError as exc:
        raise ValueError(exc.detail) from exc
    return {
        "candidate_id": candidate_id,
        "behavior_instruction_id": instruction.id,
        "status": "promoted",
        "pinned": pinned,
    }


def memory_reject_candidate(
    *, conn: sqlite3.Connection, candidate_id: str, **_kwargs: Any
) -> dict[str, Any]:
    return candidate_payload(reject_memory_candidate(conn, candidate_id=candidate_id))


def memory_list_maintenance_events(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    statuses: list[str] | None = None,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MaintenanceEventStatus  # noqa: PLC0415

    parsed = [MaintenanceEventStatus(item) for item in statuses] if statuses else None
    return {
        "events": [
            maintenance_event_payload(event)
            for event in list_maintenance_events(
                conn,
                workspace_id=workspace_id,
                statuses=parsed,
                limit=limit,
            )
        ]
    }


def memory_resolve_maintenance_event(
    *,
    conn: sqlite3.Connection,
    event_id: str,
    status: str = "resolved",
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import MaintenanceEventStatus  # noqa: PLC0415

    event = resolve_maintenance_event(
        conn,
        event_id=event_id,
        status=MaintenanceEventStatus(status),
        resolved_at=iso_now(),
    )
    if event is None:
        raise ValueError(f"maintenance event not found: {event_id}")
    return maintenance_event_payload(event)
