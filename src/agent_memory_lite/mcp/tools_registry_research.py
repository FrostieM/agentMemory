"""Research-lab MCP tool definitions."""

from __future__ import annotations

from agent_memory_lite.mcp.tools_payloads import ToolDefinition
from agent_memory_lite.mcp.tools_research import (
    memory_add_experiment_result,
    memory_distill_insight,
    memory_register_snapshot,
    memory_update_insight,
    memory_upsert_concept,
    memory_write_experiment,
)
from agent_memory_lite.mcp.tools_research_lists import (
    memory_list_concepts,
    memory_list_insights,
    memory_list_research_agenda,
)

RESEARCH_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="memory_register_snapshot",
        description=(
            "Register or update a research data snapshot with paths, "
            "build metadata, and table counts."
        ),
        handler=memory_register_snapshot,
    ),
    ToolDefinition(
        name="memory_write_experiment",
        description=(
            "Create a planned/running research experiment linked to a theory and/or data snapshot."
        ),
        handler=memory_write_experiment,
    ),
    ToolDefinition(
        name="memory_add_experiment_result",
        description=(
            "Record an experiment result; linked theory confidence/status is updated automatically."
        ),
        handler=memory_add_experiment_result,
    ),
    ToolDefinition(
        name="memory_upsert_concept",
        description=(
            "Create or update a domain concept so research vocabulary is explicit and reusable."
        ),
        handler=memory_upsert_concept,
    ),
    ToolDefinition(
        name="memory_distill_insight",
        description="Promote raw episode learnings into actionable insights or open questions.",
        handler=memory_distill_insight,
    ),
    ToolDefinition(
        name="memory_update_insight",
        description="Update an existing research insight's target link or status.",
        handler=memory_update_insight,
    ),
    ToolDefinition(
        name="memory_list_research_agenda",
        description=(
            "List current snapshots, open experiments, insights, "
            "and concepts relevant to a research query."
        ),
        handler=memory_list_research_agenda,
    ),
    ToolDefinition(
        name="memory_list_concepts",
        description="List domain concepts in the project memory.",
        handler=memory_list_concepts,
    ),
    ToolDefinition(
        name="memory_list_insights",
        description="List distilled research insights and open questions.",
        handler=memory_list_insights,
    ),
)
