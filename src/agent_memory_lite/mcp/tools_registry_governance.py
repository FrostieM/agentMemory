"""Capability + behavior + feedback MCP tool definitions."""

from __future__ import annotations

from agent_memory_lite.mcp.tools_archive import memory_archive
from agent_memory_lite.mcp.tools_behavior import (
    memory_list_behavior_instructions,
    memory_record_usage_feedback,
    memory_upsert_behavior_instruction,
)
from agent_memory_lite.mcp.tools_capabilities import (
    memory_link_capability,
    memory_list_agent_capabilities,
    memory_list_capability_links,
    memory_upsert_agent_playbook,
    memory_upsert_agent_role,
    memory_upsert_agent_skill,
)
from agent_memory_lite.mcp.tools_payloads import ToolDefinition

GOVERNANCE_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="memory_link_capability",
        description=(
            "Link a role, skill, or playbook to a theory, experiment, "
            "evidence item, insight, candidate, or decision."
        ),
        handler=memory_link_capability,
    ),
    ToolDefinition(
        name="memory_list_capability_links",
        description=(
            "List capability links that explain which roles/skills/playbooks "
            "influence research memory objects."
        ),
        handler=memory_list_capability_links,
    ),
    ToolDefinition(
        name="memory_upsert_agent_role",
        description=(
            "Create or update a first-class agent role with responsibilities and boundaries."
        ),
        handler=memory_upsert_agent_role,
    ),
    ToolDefinition(
        name="memory_upsert_agent_skill",
        description=(
            "Create or update a reusable agent skill with inputs, outputs, and related roles."
        ),
        handler=memory_upsert_agent_skill,
    ),
    ToolDefinition(
        name="memory_upsert_agent_playbook",
        description=(
            "Create or update a repeatable agent playbook with triggers, "
            "steps, and success criteria."
        ),
        handler=memory_upsert_agent_playbook,
    ),
    ToolDefinition(
        name="memory_list_agent_capabilities",
        description="List relevant roles, skills, and playbooks for a query.",
        handler=memory_list_agent_capabilities,
    ),
    ToolDefinition(
        name="memory_upsert_behavior_instruction",
        description=(
            "Create or update a persistent behavior instruction with explicit "
            "scope, priority, and conflict policy."
        ),
        handler=memory_upsert_behavior_instruction,
    ),
    ToolDefinition(
        name="memory_list_behavior_instructions",
        description=(
            "List persistent behavior instructions that should shape agent "
            "communication and operating behavior."
        ),
        handler=memory_list_behavior_instructions,
    ),
    ToolDefinition(
        name="memory_record_usage_feedback",
        description=(
            "Record whether a retrieved memory item was helpful or noisy "
            "so future ranking can improve."
        ),
        handler=memory_record_usage_feedback,
    ),
    ToolDefinition(
        name="memory_archive",
        description=(
            "Archive (or restore) a memory object so it stops appearing in "
            "get_context retrieval but stays searchable. Works across kinds: "
            "chunk, episode, file, decision, theory, insight, role, skill, "
            "playbook, behavior_instruction, candidate. Pass archive=false "
            "to restore."
        ),
        handler=memory_archive,
    ),
)
