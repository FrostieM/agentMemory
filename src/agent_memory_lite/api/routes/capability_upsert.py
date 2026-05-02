"""Upsert routes for roles / skills / playbooks.

Split out of ``capabilities.py`` so the routes file stays under the
SLOC ceiling. ``capabilities.py`` mounts this router on the same
prefix.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.routes.capability_responses import (
    to_playbook_response,
    to_role_response,
    to_skill_response,
)
from agent_memory_lite.api.schemas.capabilities import (
    AgentPlaybookResponse,
    AgentRoleResponse,
    AgentSkillResponse,
    UpsertAgentPlaybookRequest,
    UpsertAgentRoleRequest,
    UpsertAgentSkillRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.models.capabilities import (
    AgentPlaybookIn,
    AgentRoleIn,
    AgentSkillIn,
)

router = APIRouter()


@router.post("/memory/upsert_agent_role", response_model=AgentRoleResponse)
def upsert_agent_role_route(
    body: UpsertAgentRoleRequest, conn: DbDep, settings: SettingsDep
) -> AgentRoleResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/upsert_agent_role",
        operation="upsert_role",
        label="Upsert role",
        snippet=body.name,
    ) as trace:
        trace.stage_done("validate", "Role payload accepted", snippet=body.name)
        trace.stage_started("persist", "Persist role")
        role = upsert_agent_role(
            conn,
            AgentRoleIn(
                workspace_id=body.workspace_id,
                name=body.name,
                purpose=body.purpose,
                responsibilities=body.responsibilities,
                boundaries=body.boundaries,
                handoff_triggers=body.handoff_triggers,
                tools=body.tools,
                source_episode_id=body.source_episode_id,
                confidence=body.confidence,
                active=body.active,
            ),
        )
        trace.stage_done("persist", "Role persisted", counts={"active": role.active})
        trace.graph_delta(
            object_type="role", object_id=role.id, action="upserted", label="Role updated"
        )
        response = to_role_response(role)
        trace.stage_done("response", "Role response ready", counts={"role_id": role.id})
        return response


@router.post("/memory/upsert_agent_skill", response_model=AgentSkillResponse)
def upsert_agent_skill_route(
    body: UpsertAgentSkillRequest, conn: DbDep, settings: SettingsDep
) -> AgentSkillResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/upsert_agent_skill",
        operation="upsert_skill",
        label="Upsert skill",
        snippet=body.name,
    ) as trace:
        trace.stage_done("validate", "Skill payload accepted", snippet=body.name)
        trace.stage_started("persist", "Persist skill")
        skill = upsert_agent_skill(
            conn,
            AgentSkillIn(
                workspace_id=body.workspace_id,
                name=body.name,
                summary=body.summary,
                when_to_use=body.when_to_use,
                inputs=body.inputs,
                outputs=body.outputs,
                tools=body.tools,
                related_roles=body.related_roles,
                source_episode_id=body.source_episode_id,
                confidence=body.confidence,
                active=body.active,
            ),
        )
        trace.stage_done("persist", "Skill persisted", counts={"active": skill.active})
        trace.graph_delta(
            object_type="skill", object_id=skill.id, action="upserted", label="Skill updated"
        )
        response = to_skill_response(skill)
        trace.stage_done("response", "Skill response ready", counts={"skill_id": skill.id})
        return response


@router.post("/memory/upsert_agent_playbook", response_model=AgentPlaybookResponse)
def upsert_agent_playbook_route(
    body: UpsertAgentPlaybookRequest, conn: DbDep, settings: SettingsDep
) -> AgentPlaybookResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/upsert_agent_playbook",
        operation="upsert_playbook",
        label="Upsert playbook",
        snippet=body.name,
    ) as trace:
        trace.stage_done("validate", "Playbook payload accepted", snippet=body.name)
        trace.stage_started("persist", "Persist playbook")
        playbook = upsert_agent_playbook(
            conn,
            AgentPlaybookIn(
                workspace_id=body.workspace_id,
                name=body.name,
                goal=body.goal,
                triggers=body.triggers,
                steps=body.steps,
                success_criteria=body.success_criteria,
                required_skills=body.required_skills,
                source_episode_id=body.source_episode_id,
                confidence=body.confidence,
                active=body.active,
            ),
        )
        trace.stage_done("persist", "Playbook persisted", counts={"active": playbook.active})
        trace.graph_delta(
            object_type="playbook",
            object_id=playbook.id,
            action="upserted",
            label="Playbook updated",
        )
        response = to_playbook_response(playbook)
        trace.stage_done("response", "Playbook response ready", counts={"playbook_id": playbook.id})
        return response
