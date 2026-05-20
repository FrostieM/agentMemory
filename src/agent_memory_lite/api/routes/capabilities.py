"""Agent capability memory routes (list endpoint + outcome reporting).

Upserts live in ``capability_upsert.py``; this module mounts the
upsert router on the same prefix and owns the read-side listing
endpoint plus the v1.5 outcome-recording endpoint.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.api.routes.capability_responses import (
    to_playbook_response,
    to_role_response,
    to_skill_response,
)
from agent_memory_lite.api.routes.capability_upsert import router as upsert_router
from agent_memory_lite.api.schemas.capabilities import (
    ListAgentCapabilitiesRequest,
    ListAgentCapabilitiesResponse,
    RecordCapabilityOutcomeRequest,
    RecordCapabilityOutcomeResponse,
)
from agent_memory_lite.capability.usage_tracker import (
    SUPPORTED_KINDS,
    get_maturity_snapshot,
    record_invocation,
    record_outcome,
)
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities

router = APIRouter()
router.include_router(upsert_router)


def _mark_capabilities_used(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    role_ids: list[str],
    skill_ids: list[str],
    playbook_ids: list[str],
) -> None:
    for rid in role_ids:
        record_invocation(conn, workspace_id=workspace_id, kind="role", capability_id=rid)
    for sid in skill_ids:
        record_invocation(conn, workspace_id=workspace_id, kind="skill", capability_id=sid)
    for pid in playbook_ids:
        record_invocation(conn, workspace_id=workspace_id, kind="playbook", capability_id=pid)
    conn.commit()


@router.post("/memory/list_agent_capabilities", response_model=ListAgentCapabilitiesResponse)
def list_agent_capabilities_route(
    body: ListAgentCapabilitiesRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListAgentCapabilitiesResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    capabilities = build_agent_capabilities(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        include_inactive=body.include_inactive,
        limit=body.limit,
    )
    if body.mark_used and settings.capability_maturity_enabled:
        # mark_used is a write — guard with the writable check, not readable.
        ensure_workspace_writable(body.workspace_id, settings)
        _mark_capabilities_used(
            conn,
            workspace_id=body.workspace_id,
            role_ids=[r.id for r in capabilities.roles],
            skill_ids=[s.id for s in capabilities.skills],
            playbook_ids=[p.id for p in capabilities.playbooks],
        )
    return ListAgentCapabilitiesResponse(
        roles=[to_role_response(item) for item in capabilities.roles],
        skills=[to_skill_response(item) for item in capabilities.skills],
        playbooks=[to_playbook_response(item) for item in capabilities.playbooks],
    )


@router.post("/memory/capability/record_outcome", response_model=RecordCapabilityOutcomeResponse)
def record_capability_outcome_route(
    body: RecordCapabilityOutcomeRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> RecordCapabilityOutcomeResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    if body.kind not in SUPPORTED_KINDS:
        # v3.5 sector-4 audit-followup: raw ValueError escaped as 500.
        # Convert to typed ValidationError → 400 envelope.
        raise ValidationError(f"unsupported capability kind: {body.kind!r}")
    updated = record_outcome(
        conn,
        workspace_id=body.workspace_id,
        kind=body.kind,
        capability_id=body.capability_id,
        success=body.success,
        episode_id=body.episode_id,
    )
    if updated:
        conn.commit()
    snap = get_maturity_snapshot(
        conn,
        workspace_id=body.workspace_id,
        kind=body.kind,
        capability_id=body.capability_id,
    )
    if snap is None:
        # Caller asked about a capability that doesn't exist; honour the
        # contract by returning a deterministic empty snapshot rather than
        # raising — clients can inspect ``updated`` for the truth.
        return RecordCapabilityOutcomeResponse(
            capability_id=body.capability_id,
            kind=body.kind,
            updated=False,
            success_count=0,
            failure_count=0,
            usage_count=0,
        )
    return RecordCapabilityOutcomeResponse(
        capability_id=body.capability_id,
        kind=body.kind,
        updated=updated,
        success_count=snap.success_count,
        failure_count=snap.failure_count,
        usage_count=snap.usage_count,
    )
