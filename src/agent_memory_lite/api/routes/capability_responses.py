"""Wire-shape converters for capability routes.

Split out of ``capabilities.py`` so the route file stays under the
SLOC ceiling. Each helper copies a domain model into its wire schema;
reused by upsert + list routes.
"""

from __future__ import annotations

from agent_memory_lite.api.schemas.capabilities import (
    AgentPlaybookResponse,
    AgentRoleResponse,
    AgentSkillResponse,
)
from agent_memory_lite.models.capabilities import AgentPlaybook, AgentRole, AgentSkill


def to_role_response(role: AgentRole) -> AgentRoleResponse:
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


def to_skill_response(skill: AgentSkill) -> AgentSkillResponse:
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


def to_playbook_response(playbook: AgentPlaybook) -> AgentPlaybookResponse:
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
