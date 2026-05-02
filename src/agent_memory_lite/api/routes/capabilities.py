"""Agent capability memory routes (list endpoint).

Upserts live in ``capability_upsert.py``; this module mounts the
upsert router on the same prefix and owns the read-side listing
endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.capability_responses import (
    to_playbook_response,
    to_role_response,
    to_skill_response,
)
from agent_memory_lite.api.routes.capability_upsert import router as upsert_router
from agent_memory_lite.api.schemas.capabilities import (
    ListAgentCapabilitiesRequest,
    ListAgentCapabilitiesResponse,
)
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities

router = APIRouter()
router.include_router(upsert_router)


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
    return ListAgentCapabilitiesResponse(
        roles=[to_role_response(item) for item in capabilities.roles],
        skills=[to_skill_response(item) for item in capabilities.skills],
        playbooks=[to_playbook_response(item) for item in capabilities.playbooks],
    )
