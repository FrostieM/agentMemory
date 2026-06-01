"""MCP stdio server for the v3 compact memory surface."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import mcp.server.stdio
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from agent_memory_lite.config.local_only_guard import assert_local_only
from agent_memory_lite.config.offline_bootstrap import maybe_configure_offline
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.logging_setup import configure_logging, get_logger
from agent_memory_lite.mcp.stdio_guards import _workspace_from_args
from agent_memory_lite.mcp.stdio_handlers import _HANDLERS
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.stdio_tools import ALL_TOOLS
from agent_memory_lite.version import __version__

__all__ = [
    "_HANDLERS",
    "_TOOLS",
    "_runtime",
    "_warm_embeddings",
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


@_call_tool_decorator
async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    args = arguments or {}
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


def _warm_embeddings(settings: Settings) -> None:
    """Load the embedding model SYNCHRONOUSLY, on the MAIN thread, at startup.

    The sentence-transformers import (which transitively pulls scipy/sklearn)
    MUST run on the main thread. In a background daemon thread it deadlocks on
    Python 3.14's import machinery while the event loop is active -- observed via
    py-spy: the ``amem-embed-warmup`` thread wedged mid-``import`` for the whole
    session, holding the import locks and ``_load_lock`` so even unrelated,
    SQL-only tool calls (``memory_impact_check``) hung for minutes. Importing
    here, before ``_server.run()`` and before any worker thread exists, is
    single-threaded and deadlock-free. With the venv on the AV exclusion list the
    import is a few seconds -- inside the MCP client's connect timeout -- so
    briefly blocking startup is the right trade. Failure-soft."""
    if not settings.mcp_warm_embed:
        return
    try:
        _runtime.provider().embed_batch(["warmup"])
    except Exception as exc:  # warm-up must never crash the server
        _log.debug("embed_warmup_failed", error=str(exc))


async def _run() -> None:
    settings = _runtime.settings
    configure_logging(settings.log_level)
    assert_local_only(settings)
    # Default HF to offline once the embedding model is cached -- before the
    # first tool handler triggers a lazy embedding load in this MCP process.
    maybe_configure_offline(settings)
    # Warm the embedding model SYNCHRONOUSLY on the main thread before serving.
    # The sentence-transformers import must NOT run in a daemon thread -- on
    # Python 3.14 it deadlocks while the event loop is active (see
    # _warm_embeddings). The main thread, before any worker exists, is the only
    # safe place; with the venv AV-excluded the import fits the connect timeout.
    _warm_embeddings(settings)
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
