"""Theory memory routes (write + evidence). Listing lives in ``theory_list.py``."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.routes._capability_suggest_payload import (
    capability_suggestion_payloads,
)
from agent_memory_lite.api.routes.theory_list import router as list_router
from agent_memory_lite.api.routes.theory_responses import (
    evidence_in_from_body,
    theory_in_from_body,
    to_evidence_response,
    to_theory_response,
)
from agent_memory_lite.api.schemas.theories import (
    AddTheoryEvidenceRequest,
    TheoryEvidenceResponse,
    TheoryResponse,
    WriteTheoryRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion._write_helpers import resolve_source_episode_id
from agent_memory_lite.ingestion.theory_writer import add_theory_evidence, write_theory

router = APIRouter()
# Mount the listing route on the same prefix so callers see one
# unified surface even though the implementation is split.
router.include_router(list_router)


@router.post("/memory/write_theory", response_model=TheoryResponse)
def write_theory_route(
    body: WriteTheoryRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> TheoryResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/write_theory",
        operation="write_theory",
        label="Write theory",
        snippet=body.title,
    ) as trace:
        trace.stage_done(
            "validate",
            "Theory payload accepted",
            counts={
                "validation_criteria": len(body.validation_criteria),
                "predictions": len(body.predictions),
            },
            snippet=body.title,
        )
        # 2.2 Move 1: auto-thread source_episode_id (shared helper).
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
        trace.stage_started("persist", "Persist theory")
        theory = write_theory(conn, theory_in_from_body(body, source_episode_id=source_episode_id))
        trace.stage_done("persist", "Theory persisted", counts={"status": theory.status})
        trace.graph_delta(
            object_type="theory",
            object_id=theory.id,
            action="created",
            label="Theory written",
        )
        # Move 4 (v2.2): rank workspace capabilities by token overlap
        # with the theory's title + claim + mechanism and surface top-3
        # in the response. Read-only hint, never auto-links — same
        # contract as Move 3 for decisions.
        response = to_theory_response(theory)
        response.capability_suggestions = capability_suggestion_payloads(
            conn,
            workspace_id=body.workspace_id,
            title=body.title,
            text=body.claim,
            rationale=body.mechanism,
        )
        trace.stage_done(
            "response",
            "Theory response ready",
            counts={
                "theory_id": theory.id,
                "capability_suggestions": len(response.capability_suggestions),
            },
        )
        return response


@router.post("/memory/add_theory_evidence", response_model=TheoryEvidenceResponse)
def add_theory_evidence_route(
    body: AddTheoryEvidenceRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> TheoryEvidenceResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/add_theory_evidence",
        operation="add_theory_evidence",
        label="Add theory evidence",
        snippet=body.summary,
    ) as trace:
        trace.stage_done(
            "validate",
            "Evidence payload accepted",
            counts={"kind": body.kind, "has_metrics": bool(body.metrics)},
            snippet=body.summary,
        )
        trace.stage_started("persist", "Persist theory evidence")
        evidence = add_theory_evidence(conn, evidence_in_from_body(body))
        trace.stage_done("persist", "Theory evidence persisted", counts={"kind": evidence.kind})
        trace.graph_delta(
            object_type="theory_evidence",
            object_id=evidence.id,
            action="created",
            label="Theory evidence added",
            counts={"theory_id": evidence.theory_id},
        )
        response = to_evidence_response(evidence)
        trace.stage_done(
            "response", "Theory evidence response ready", counts={"evidence_id": evidence.id}
        )
        return response
