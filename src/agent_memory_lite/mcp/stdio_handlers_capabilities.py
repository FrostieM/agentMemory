"""Handlers for agent capabilities (roles/skills/playbooks) + record_usage_feedback."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.maintenance.usage_feedback import record_usage_feedback
from agent_memory_lite.mcp.stdio_guards import _with_workspace, _workspace_from_args
from agent_memory_lite.mcp.stdio_http import _http_write
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.models.capabilities import AgentPlaybookIn, AgentRoleIn, AgentSkillIn
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities


def _handle_upsert_agent_role(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/upsert_agent_role", payload, log_label="upsert_agent_role")
    if delegated is not None:
        return delegated

    role = upsert_agent_role(_runtime.db(), AgentRoleIn(**payload))
    return {
        "role_id": role.id,
        "name": role.name,
        "confidence": role.confidence,
        "active": role.active,
        "updated_at": role.updated_at,
    }


def _handle_upsert_agent_skill(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/upsert_agent_skill", payload, log_label="upsert_agent_skill")
    if delegated is not None:
        return delegated

    skill = upsert_agent_skill(_runtime.db(), AgentSkillIn(**payload))
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "confidence": skill.confidence,
        "active": skill.active,
        "updated_at": skill.updated_at,
    }


def _handle_upsert_agent_playbook(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write(
        "/memory/upsert_agent_playbook",
        payload,
        log_label="upsert_agent_playbook",
    )
    if delegated is not None:
        return delegated

    playbook = upsert_agent_playbook(_runtime.db(), AgentPlaybookIn(**payload))
    return {
        "playbook_id": playbook.id,
        "name": playbook.name,
        "confidence": playbook.confidence,
        "active": playbook.active,
        "updated_at": playbook.updated_at,
    }


def _handle_list_agent_capabilities(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    capabilities = build_agent_capabilities(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        query=args.get("query"),
        include_inactive=bool(args.get("include_inactive", False)),
        limit=int(args.get("limit", 6)),
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


def _handle_record_usage_feedback(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write(
        "/memory/record_usage_feedback",
        payload,
        log_label="record_usage_feedback",
    )
    if delegated is not None:
        return delegated

    feedback = record_usage_feedback(
        _runtime.db_for(str(payload["workspace_id"])),
        workspace_id=str(payload["workspace_id"]),
        source_type=str(payload.get("source_type", "chunk")),
        source_id=str(payload["source_id"]),
        query=str(payload.get("query", "")),
        usefulness=float(payload["usefulness"]),
        task_id=payload.get("task_id"),
        notes=str(payload.get("notes", "")),
    )
    return feedback.to_dict()
