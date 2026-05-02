"""Capability-link + agent role/skill/playbook MCP tool handlers."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.mcp.tools_payloads import capability_link_payload
from agent_memory_lite.models.capabilities import (
    AgentPlaybookIn,
    AgentRoleIn,
    AgentSkillIn,
)
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities
from agent_memory_lite.repositories.capability_links_repo import list_capability_links


def memory_link_capability(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    return capability_link_payload(link_capability(conn, CapabilityLinkIn(**payload)))


def memory_list_capability_links(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    target_type: str | None = None,
    target_id: str | None = None,
    capability_type: str | None = None,
    capability_id: str | None = None,
    limit: int = 50,
    **_kwargs: Any,
) -> dict[str, Any]:
    from agent_memory_lite.models.enums import (  # noqa: PLC0415
        CapabilityLinkTargetType,
        CapabilityType,
    )

    return {
        "links": [
            capability_link_payload(link)
            for link in list_capability_links(
                conn,
                workspace_id=workspace_id,
                target_type=CapabilityLinkTargetType(target_type) if target_type else None,
                target_id=target_id,
                capability_type=CapabilityType(capability_type) if capability_type else None,
                capability_id=capability_id,
                limit=limit,
            )
        ]
    }


def memory_upsert_agent_role(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    role = upsert_agent_role(conn, AgentRoleIn(**payload))
    return {
        "role_id": role.id,
        "name": role.name,
        "confidence": role.confidence,
        "active": role.active,
    }


def memory_upsert_agent_skill(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    skill = upsert_agent_skill(conn, AgentSkillIn(**payload))
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "confidence": skill.confidence,
        "active": skill.active,
    }


def memory_upsert_agent_playbook(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    playbook = upsert_agent_playbook(conn, AgentPlaybookIn(**payload))
    return {
        "playbook_id": playbook.id,
        "name": playbook.name,
        "confidence": playbook.confidence,
        "active": playbook.active,
    }


def memory_list_agent_capabilities(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 6,
    **_kwargs: Any,
) -> dict[str, Any]:
    capabilities = build_agent_capabilities(
        conn,
        workspace_id=workspace_id,
        query=query,
        include_inactive=include_inactive,
        limit=limit,
    )
    return {
        "roles": [
            {
                "role_id": item.id,
                "name": item.name,
                "purpose": item.purpose,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.roles
        ],
        "skills": [
            {
                "skill_id": item.id,
                "name": item.name,
                "summary": item.summary,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.skills
        ],
        "playbooks": [
            {
                "playbook_id": item.id,
                "name": item.name,
                "goal": item.goal,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in capabilities.playbooks
        ],
    }
