"""MCP stdio tool schemas for the v3 compact memory surface."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_tools_memory import MEMORY_TOOLS

ALL_TOOLS: list[types.Tool] = list(MEMORY_TOOLS)
