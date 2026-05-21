"""MCP stdio server.

Exposes the same service functions used by the HTTP routes as MCP tools so
Claude Code, Cursor, or any other MCP-aware agent can discover them as
first-class tool calls without needing the HTTP service to be up.

Run via::

    python -m agent_memory_lite.mcp.stdio_server

Tool schemas, handlers, runtime state, HTTP delegation, payloads, and
guards live in the per-domain ``stdio_*`` sibling modules. This module
is the dispatch shim the framework registers.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import mcp.server.stdio
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from agent_memory_lite.logging_setup import configure_logging, get_logger
from agent_memory_lite.mcp.stdio_guards import _workspace_from_args
from agent_memory_lite.mcp.stdio_handlers import _HANDLERS
from agent_memory_lite.mcp.stdio_handlers_capabilities import _handle_upsert_agent_role
from agent_memory_lite.mcp.stdio_handlers_decisions import _handle_write_decision
from agent_memory_lite.mcp.stdio_handlers_episodes import (
    _handle_get_context,
    _handle_ingest_episode,
    _handle_ingest_file,
    _handle_search,
)
from agent_memory_lite.mcp.stdio_handlers_memory import MEMORY_HANDLERS
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.stdio_tools import ALL_TOOLS
from agent_memory_lite.mcp.v2_compat import _WRITE_COMPAT_TOOLS, compat_dispatch
from agent_memory_lite.mcp.v2_compat import is_enabled as v2_compat_enabled
from agent_memory_lite.version import __version__

__all__ = [
    "_HANDLERS",
    "_TOOLS",
    "_handle_get_context",
    "_handle_ingest_episode",
    "_handle_ingest_file",
    "_handle_search",
    "_handle_upsert_agent_role",
    "_handle_write_decision",
    "_runtime",
    "_workspace_from_args",
    "main",
]

_log = get_logger("mcp.stdio_server")
_TOOLS: list[types.Tool] = ALL_TOOLS

_server: Server = Server("agent-memory-lite")
_ListToolsHandler = Callable[[], Awaitable[list[types.Tool]]]
_ListToolsDecorator = Callable[[_ListToolsHandler], _ListToolsHandler]
_CallToolHandler = Callable[[str, dict[str, Any] | None], Awaitable[list[types.TextContent]]]
_CallToolDecorator = Callable[[_CallToolHandler], _CallToolHandler]
_list_tools_factory = cast(Callable[[], object], _server.list_tools)
_call_tool_factory = cast(Callable[[], object], _server.call_tool)
_list_tools_decorator = cast(_ListToolsDecorator, _list_tools_factory())
_call_tool_decorator = cast(_CallToolDecorator, _call_tool_factory())


@_list_tools_decorator
async def _list_tools() -> list[types.Tool]:
    return _TOOLS


def _maybe_compat_dispatch(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Route ``name`` through the v2-to-canonical compat shim when enabled.

    Returns an envelope dict when the shim handled the call, ``None``
    otherwise (caller falls through to the native v2 handler).

    Gate: ``MEMORY_V2_COMPAT_ENABLED`` env, **default ON** post 2026-05-18
    canonical-rename. The shim translates legacy v2 names
    (``memory_record_with_evidence``, ``memory_write_decision``, ...) onto
    the canonical backend (``memory_write``) so they no longer drag the
    slow Ollama-extraction pipeline into the agent's hot path.
    """
    if not v2_compat_enabled():
        return None
    # The shim only handles legacy v2 names that need translation to
    # the canonical backend. Canonical names (memory_search,
    # memory_write, memory_get, etc.) always hit MEMORY_HANDLERS directly.
    if name in MEMORY_HANDLERS:
        return None
    ws = args.get("workspace_id") if isinstance(args, dict) else None
    # Round-2 re-audit (CRITICAL): the workspace-isolation guard MUST run
    # here, before the try/except below. When it lived inside
    # compat_dispatch its ValueError was caught by the `except Exception`
    # ("shim must never raise"), which returned None — falling the call
    # THROUGH to the separately-registered native v2 handler and
    # re-opening the cross-workspace write hole. On a blocked write we
    # return an explicit error ENVELOPE so _call_tool surfaces it and
    # never reaches the native handler.
    if ws and name in _WRITE_COMPAT_TOOLS:
        from agent_memory_lite.mcp.stdio_guards import (  # noqa: PLC0415
            _ensure_workspace_writable,
        )

        try:
            _ensure_workspace_writable(str(ws))
        except ValueError as exc:
            return {
                "ok": False,
                "error": {"code": "workspace_isolation_blocked", "message": str(exc)},
            }
    try:
        # Route via registry when the caller passed an explicit workspace_id
        # so a misconfigured anchor doesn't silently target the wrong DB.
        # Anchor remains the fallback for legacy callers that omit it.
        conn = _runtime.db_for(str(ws)) if ws else _runtime.db()
        return compat_dispatch(conn, name, args)
    except Exception as exc:  # shim must never raise into the dispatcher
        _log.warning("v2_compat_dispatch_failed", tool=name, error=str(exc))
        return None


@_call_tool_decorator
async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    args = arguments or {}
    # Compat shim runs FIRST when enabled — translates a v2 tool name
    # into a canonical backend call before the native v2 handler sees the request.
    shim_result = await asyncio.to_thread(_maybe_compat_dispatch, name, args)
    if shim_result is not None:
        return [types.TextContent(type="text", text=json.dumps(shim_result, ensure_ascii=False))]
    if name not in _HANDLERS:
        return [types.TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}))]
    try:
        result = await asyncio.to_thread(_HANDLERS[name], args)
    except Exception as exc:
        _log.error("mcp_tool_error", tool=name, error=str(exc))
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
            )
        ]
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


async def _run() -> None:
    settings = _runtime.settings
    configure_logging(settings.log_level)
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await _server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="agent-memory-lite",
                server_version=__version__,
                capabilities=_server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> int:
    try:
        asyncio.run(_run())
    finally:
        _runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
