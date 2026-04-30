"""Behavior instruction memory routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_allowed
from agent_memory_lite.api.schemas.behavior import (
    BehaviorInstructionResponse,
    ListBehaviorInstructionsRequest,
    ListBehaviorInstructionsResponse,
    UpsertBehaviorInstructionRequest,
)
from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionIn
from agent_memory_lite.repositories.behavior_repo import list_behavior_instructions

router = APIRouter()


def _instruction_response(item: BehaviorInstruction) -> BehaviorInstructionResponse:
    return BehaviorInstructionResponse(
        instruction_id=item.id,
        workspace_id=item.workspace_id,
        name=item.name,
        rule=item.rule,
        kind=item.kind,
        scope=item.scope,
        priority=item.priority,
        rationale=item.rationale,
        applies_to=item.applies_to,
        conflict_policy=item.conflict_policy,
        source_episode_id=item.source_episode_id,
        source_type=item.source_type,
        source_id=item.source_id,
        reviewed_by=item.reviewed_by,
        reviewed_at=item.reviewed_at,
        expires_at=item.expires_at,
        conflict_group=item.conflict_group,
        confidence=item.confidence,
        active=item.active,
        last_applied_at=item.last_applied_at,
        application_count=item.application_count,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.post("/memory/upsert_behavior_instruction", response_model=BehaviorInstructionResponse)
def upsert_behavior_instruction_route(
    body: UpsertBehaviorInstructionRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> BehaviorInstructionResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    instruction = upsert_behavior_instruction(
        conn,
        BehaviorInstructionIn(
            workspace_id=body.workspace_id,
            name=body.name,
            rule=body.rule,
            kind=body.kind,
            scope=body.scope,
            priority=body.priority,
            rationale=body.rationale,
            applies_to=body.applies_to,
            conflict_policy=body.conflict_policy,
            source_episode_id=body.source_episode_id,
            source_type=body.source_type,
            source_id=body.source_id,
            reviewed_by=body.reviewed_by,
            reviewed_at=body.reviewed_at,
            expires_at=body.expires_at,
            conflict_group=body.conflict_group,
            confidence=body.confidence,
            active=body.active,
        ),
    )
    return _instruction_response(instruction)


@router.post("/memory/list_behavior_instructions", response_model=ListBehaviorInstructionsResponse)
def list_behavior_instructions_route(
    body: ListBehaviorInstructionsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListBehaviorInstructionsResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    return ListBehaviorInstructionsResponse(
        instructions=[
            _instruction_response(item)
            for item in list_behavior_instructions(
                conn,
                workspace_id=body.workspace_id,
                query=body.query,
                kinds=body.kinds,
                include_inactive=body.include_inactive,
                limit=body.limit,
            )
        ]
    )
