"""Tool definitions exposed via MCP.

Each tool maps to the same service function used by the HTTP routes. The
JSON schema is intentionally loose; the underlying functions own validation.

Per-domain handlers live in ``tools_*.py`` siblings. The ``TOOLS``
registry lives in ``tools_registry*.py`` and is re-exported here so
existing imports of ``mcp.tools.TOOLS`` keep working.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.tools_payloads import ToolDefinition, ToolHandler
from agent_memory_lite.mcp.tools_registry import TOOLS

__all__ = ["TOOLS", "ToolDefinition", "ToolHandler", "dispatch"]


def dispatch(name: str, **kwargs: Any) -> dict[str, Any]:
    for tool in TOOLS:
        if tool.name == name:
            return tool.handler(**kwargs)
    raise KeyError(f"unknown MCP tool: {name!r}")
