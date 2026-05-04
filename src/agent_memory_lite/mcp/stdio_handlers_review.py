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


def _handle_promote_candidate_to_behavior(args: dict[str, Any]) -> dict[str, Any]:
    """v1.10 MCP tool: promote a CORRECTION candidate to a behavior_instruction.

    Mirrors the HTTP route ``/memory/promote_candidate_to_behavior`` but
    runs locally against the runtime connection so MCP-only deployments
    can complete the correction loop without standing up the HTTP service.
    """
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

    workspace_id = _workspace_from_args(args, intent="write")
    # Use explicit None checks so a falsy-but-present "kind"=False stays
    # an error rather than silently falling back to the default.
    payload = CorrectionPromoteInput(
        workspace_id=workspace_id,
        candidate_id=str(args["candidate_id"]),
        name=str(args["name"]),
        rule_text_override=args.get("rule_text_override") or None,
        rationale=str(args.get("rationale") or ""),
        kind=BehaviorInstructionKind(args.get("kind", "operating_rule")),
        scope=BehaviorInstructionScope(args.get("scope", "workspace")),
        priority=BehaviorInstructionPriority(args.get("priority", "user_preference")),
        conflict_policy=BehaviorConflictPolicy(args.get("conflict_policy", "current_user_wins")),
        applies_to=coerce_applies_to(args.get("applies_to")),
        decided_by=args.get("decided_by"),
        pinned=bool(args.get("pinned", False)),
        overwrite=bool(args.get("overwrite", False)),
    )
    try:
        instruction = promote_correction_to_behavior(_runtime.db_for(workspace_id), payload)
    except CorrectionPromotionError as exc:
        raise ValueError(exc.detail) from exc
    return {
        "candidate_id": payload.candidate_id,
        "behavior_instruction_id": instruction.id,
        "status": "promoted",
        "pinned": payload.pinned,
    }


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
