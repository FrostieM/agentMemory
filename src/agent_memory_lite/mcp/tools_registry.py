"""TOOLS tuple — the public registry of MCP handlers.

The actual definitions are split by domain across:
- ``tools_registry_core.py`` — episodes, review, decisions, theories
- ``tools_registry_research.py`` — research-lab tools
- ``tools_registry_governance.py`` — capabilities, behavior, feedback
"""

from __future__ import annotations

from agent_memory_lite.mcp.tools_payloads import ToolDefinition
from agent_memory_lite.mcp.tools_registry_core import CORE_TOOLS
from agent_memory_lite.mcp.tools_registry_governance import GOVERNANCE_TOOLS
from agent_memory_lite.mcp.tools_registry_research import RESEARCH_TOOLS

TOOLS: tuple[ToolDefinition, ...] = (
    *CORE_TOOLS,
    *GOVERNANCE_TOOLS,
    *RESEARCH_TOOLS,
)
