"""MCP stdio dispatch table for the v3 compact memory surface."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_handlers_memory import MEMORY_HANDLERS

_HANDLERS: dict[str, Any] = dict(MEMORY_HANDLERS)
