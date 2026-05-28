"""v1.10: HTTP route for promote_candidate_to_behavior.

Thin wrapper around ``ingestion/correction_promotion.py`` so HTTP and
MCP-stdio promotion paths share the same service. The route only owns
schema validation, settings injection, and translation of
``CorrectionPromotionError`` into HTTP status codes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.schemas.promote_to_behavior import (
    PromoteCandidateToBehaviorRequest,
    PromoteCandidateToBehaviorResponse,
)
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.ingestion.correction_promotion import (
    CorrectionPromoteInput,
    CorrectionPromotionError,
    promote_correction_to_behavior,
)

router = APIRouter()

_HTTP_STATUS_FOR_CODE = {
    "not_found": 404,
    "already_decided": 409,
    "wrong_kind": 409,
    "name_taken": 409,
}


@router.post(
    "/memory/promote_candidate_to_behavior",
    response_model=PromoteCandidateToBehaviorResponse,
)
def promote_candidate_to_behavior_route(
    body: PromoteCandidateToBehaviorRequest,
    settings: SettingsDep,
    conn: DbDep,
) -> PromoteCandidateToBehaviorResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    ensure_workspace_matches_db(conn, body.workspace_id, settings)
    try:
        instruction = promote_correction_to_behavior(
            conn,
            CorrectionPromoteInput(
                workspace_id=body.workspace_id,
                candidate_id=body.candidate_id,
                name=body.name,
                rule_text_override=body.rule_text_override,
                rationale=body.rationale,
                kind=body.kind,
                scope=body.scope,
                priority=body.priority,
                conflict_policy=body.conflict_policy,
                applies_to=tuple(body.applies_to),
                decided_by=body.decided_by,
                pinned=body.pinned,
                overwrite=body.overwrite,
            ),
        )
    except CorrectionPromotionError as exc:
        raise HTTPException(
            status_code=_HTTP_STATUS_FOR_CODE.get(exc.code, 400),
            detail=exc.detail,
        ) from exc

    return PromoteCandidateToBehaviorResponse(
        candidate_id=body.candidate_id,
        behavior_instruction_id=instruction.id,
        status="promoted",
    )
