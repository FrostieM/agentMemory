"""MCP-style tool surface.

Phase 6 ships the tool definitions and a dispatcher; the stdio
JSON-RPC transport lives in `stdio_server.py`.

`logging.basicConfig(stream=sys.stderr, force=True)` runs as a side
effect of importing this package, BEFORE any third-party library that
might attach a handler to the root logger. The MCP stdio protocol
reserves stdout for JSON-RPC framing — a single stray log line on
stdout (e.g. sentence-transformers' "No device provided, using cpu")
corrupts the protocol and surfaces as
"could not connect to MCP server" on the client side.
"""

from __future__ import annotations

import logging
import sys

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)

from agent_memory_lite.mcp.tools import (  # noqa: E402
    TOOLS,
    ToolDefinition,
    dispatch,
)

__all__ = ["TOOLS", "ToolDefinition", "dispatch"]
