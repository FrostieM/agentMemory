"""MCP-style tool surface.

Phase 6 ships the tool definitions and a dispatcher; the actual stdio
JSON-RPC transport is intentionally minimal so callers can plug in any MCP
runtime they prefer.
"""

from agent_memory_lite.mcp.tools import (
    TOOLS,
    ToolDefinition,
    dispatch,
)

__all__ = ["TOOLS", "ToolDefinition", "dispatch"]
