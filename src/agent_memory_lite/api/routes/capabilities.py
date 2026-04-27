"""Agent capability memory routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep
from agent_memory_lite.api.schemas.capabilities import (
    AgentPlaybookResponse,
    AgentRoleResponse,
    AgentSkillResponse,
    ListAgentCapabilitiesRequest,
    ListAgentCapabilitiesResponse,
    UpsertAgentPlaybookRequest,
    UpsertAgentRoleRequest,
    UpsertAgentSkillRequest,
)
from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.models.capabilities import (
    AgentPlaybook,
    AgentPlaybookIn,
    AgentRole,
    AgentRoleIn,
    AgentSkill,
    AgentSkillIn,
)
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities

router = APIRouter()


def _role_response(role: AgentRole) -> AgentRoleResponse:
    return AgentRoleResponse(
        role_id=role.id,
        workspace_id=role.workspace_id,
        name=role.name,
        purpose=role.purpose,
        responsibilities=role.responsibilities,
        boundaries=role.boundaries,
        handoff_triggers=role.handoff_triggers,
        tools=role.tools,
        source_episode_id=role.source_episode_id,
        confidence=role.confidence,
        active=role.active,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _skill_response(skill: AgentSkill) -> AgentSkillResponse:
    return AgentSkillResponse(
        skill_id=skill.id,
        workspace_id=skill.workspace_id,
        name=skill.name,
        summary=skill.summary,
        when_to_use=skill.when_to_use,
        inputs=skill.inputs,
        outputs=skill.outputs,
        tools=skill.tools,
        related_roles=skill.related_roles,
        source_episode_id=skill.source_episode_id,
        confidence=skill.confidence,
        active=skill.active,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


def _playbook_response(playbook: AgentPlaybook) -> AgentPlaybookResponse:
    return AgentPlaybookResponse(
        playbook_id=playbook.id,
        workspace_id=playbook.workspace_id,
        name=playbook.name,
        goal=playbook.goal,
        triggers=playbook.triggers,
        steps=playbook.steps,
        success_criteria=playbook.success_criteria,
        required_skills=playbook.required_skills,
        source_episode_id=playbook.source_episode_id,
        confidence=playbook.confidence,
        active=playbook.active,
        created_at=playbook.created_at,
        updated_at=playbook.updated_at,
    )


@router.post("/memory/upsert_agent_role", response_model=AgentRoleResponse)
def upsert_agent_role_route(
    body: UpsertAgentRoleRequest,
    conn: DbDep,
) -> AgentRoleResponse:
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
    return _role_response(role)


@router.post("/memory/upsert_agent_skill", response_model=AgentSkillResponse)
def upsert_agent_skill_route(
    body: UpsertAgentSkillRequest,
    conn: DbDep,
) -> AgentSkillResponse:
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
    return _skill_response(skill)


@router.post("/memory/upsert_agent_playbook", response_model=AgentPlaybookResponse)
def upsert_agent_playbook_route(
    body: UpsertAgentPlaybookRequest,
    conn: DbDep,
) -> AgentPlaybookResponse:
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
    return _playbook_response(playbook)


@router.post("/memory/list_agent_capabilities", response_model=ListAgentCapabilitiesResponse)
def list_agent_capabilities_route(
    body: ListAgentCapabilitiesRequest,
    conn: DbDep,
) -> ListAgentCapabilitiesResponse:
    capabilities = build_agent_capabilities(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        include_inactive=body.include_inactive,
        limit=body.limit,
    )
    return ListAgentCapabilitiesResponse(
        roles=[_role_response(item) for item in capabilities.roles],
        skills=[_skill_response(item) for item in capabilities.skills],
        playbooks=[_playbook_response(item) for item in capabilities.playbooks],
    )
