"""Version-free MCP tool registry for the v3 compact memory surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from agent_memory_lite.mcp.stdio_handlers_memory import MEMORY_HANDLERS
from agent_memory_lite.mcp.stdio_tools_memory import MEMORY_TOOLS

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


def _tool_definition(tool_name: str, description: str, schema: dict[str, Any]) -> ToolDefinition:
    handler = MEMORY_HANDLERS[tool_name]
    return ToolDefinition(
        name=tool_name,
        description=description,
        input_schema=schema,
        handler=handler,
    )


TOOLS: tuple[ToolDefinition, ...] = tuple(
    _tool_definition(tool.name, tool.description or "", dict(tool.inputSchema))
    for tool in MEMORY_TOOLS
)


def dispatch(name: str, **kwargs: Any) -> dict[str, Any]:
    """Dispatch one v3 memory tool by name."""
    handler = MEMORY_HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown MCP tool: {name!r}")
    return cast(dict[str, Any], handler(kwargs))


__all__ = ["TOOLS", "ToolDefinition", "ToolHandler", "dispatch"]
