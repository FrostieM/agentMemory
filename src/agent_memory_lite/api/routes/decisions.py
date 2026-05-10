"""POST /memory/write_decision."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.routes._capability_suggest_payload import (
    capability_suggestion_payloads,
)
from agent_memory_lite.api.schemas.decisions import (
    DecisionItem,
    ListDecisionsRequest,
    ListDecisionsResponse,
    WriteDecisionRequest,
    WriteDecisionResponse,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion._write_helpers import resolve_source_episode_id
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.models.decisions import Decision, DecisionIn
from agent_memory_lite.repositories.decisions_repo import list_active_decisions, list_all_decisions
from agent_memory_lite.utils.text_encoding import repair_common_mojibake

router = APIRouter()


def _decision_item(item: Decision) -> DecisionItem:
    return DecisionItem(
        decision_id=item.id,
        title=repair_common_mojibake(item.title),
        decision_text=repair_common_mojibake(item.decision_text),
        rationale=repair_common_mojibake(item.rationale) if item.rationale else None,
        status=item.status.value,
        supersedes_decision_id=item.supersedes_decision_id,
        source_episode_id=item.source_episode_id,
        confidence=item.confidence,
        importance=item.importance,
        valid_from=item.valid_from,
        valid_to=item.valid_to,
        created_at=item.created_at,
        updated_at=item.updated_at,
        pinned=item.pinned,
        references=item.references,
    )


@router.post("/memory/write_decision", response_model=WriteDecisionResponse)
def write_decision_route(
    body: WriteDecisionRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> WriteDecisionResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/write_decision",
        operation="write_decision",
        label="Write decision",
        snippet=body.title,
    ) as trace:
        trace.stage_done(
            "validate",
            "Decision payload accepted",
            counts={"has_rationale": body.rationale is not None},
            snippet=body.title,
        )
        # 2.2 Move 1: auto-thread source_episode_id (helper in
        # ingestion/_write_helpers.py is shared with theories.py and
        # the MCP local-fallback path so all three surfaces honor the
        # same auto-thread + allow_orphan semantics).
        source_episode_id = resolve_source_episode_id(
            conn,
            workspace_id=body.workspace_id,
            explicit=body.source_episode_id,
            allow_orphan=body.allow_orphan,
            settings=settings,
        )
        if source_episode_id is not None and body.source_episode_id is None:
            trace.stage_done(
                "auto_thread",
                "Auto-filled source_episode_id from recent agent activity",
                counts={"source_episode_id": source_episode_id},
            )
        payload = DecisionIn(
            workspace_id=body.workspace_id,
            title=body.title,
            decision_text=body.decision_text,
            rationale=body.rationale,
            supersedes_decision_id=body.supersedes_decision_id,
            source_episode_id=source_episode_id,
            confidence=body.confidence,
            importance=body.importance,
            references=body.references,
        )
        trace.stage_started("persist", "Persist decision")
        decision = write_decision(conn, payload)
        trace.stage_done(
            "persist",
            "Decision persisted",
            counts={
                "status": decision.status.value,
                "supersedes": bool(decision.supersedes_decision_id),
            },
        )
        trace.graph_delta(
            object_type="decision",
            object_id=decision.id,
            action="created",
            label="Decision written",
        )
        # Move 3: rank existing capabilities by token overlap with the
        # decision's title+text+rationale and surface the top-3 in the
        # response. Read-only hint — never auto-links.
        suggestions = capability_suggestion_payloads(
            conn,
            workspace_id=body.workspace_id,
            title=body.title,
            text=body.decision_text,
            rationale=body.rationale,
        )
        response = WriteDecisionResponse(
            decision_id=decision.id,
            status=decision.status.value,
            valid_from=decision.valid_from,
            superseded_decision_id=decision.supersedes_decision_id,
            capability_suggestions=suggestions,
        )
        trace.stage_done(
            "response",
            "Decision response ready",
            counts={"decision_id": decision.id, "capability_suggestions": len(suggestions)},
        )
        return response


@router.post("/memory/list_decisions", response_model=ListDecisionsResponse)
def list_decisions_route(
    body: ListDecisionsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListDecisionsResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    if body.include_superseded:
        decisions = list_all_decisions(
            conn,
            body.workspace_id,
            query=body.query,
            limit=body.limit,
            since=body.since,
            until=body.until,
        )
    else:
        decisions = list_active_decisions(
            conn,
            body.workspace_id,
            query=body.query,
            limit=body.limit,
            since=body.since,
            until=body.until,
        )
    return ListDecisionsResponse(decisions=[_decision_item(item) for item in decisions])
