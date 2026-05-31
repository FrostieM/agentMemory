"""MCP stdio server for the v3 compact memory surface."""

from __future__ import annotations

import asyncio
import json
import threading
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
    "_maybe_warm_embeddings",
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
    # Kick the embedding warm-up off here -- on the FIRST tools/list, i.e. once
    # the stdio server is serving and the main thread is parked in the receive
    # loop. Starting it at startup blocked or deadlocked the server (see
    # _maybe_warm_embeddings); here it is instant and deadlock-free.
    _maybe_warm_embeddings(_runtime.settings)
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


# Guards the one-shot warm-up spawn: repeated tools/list calls must not each
# start a model-load thread.
_warm_started = threading.Event()


def _warm_embeddings() -> None:
    """Best-effort: load the embedding model in this daemon thread so the first
    embedding-dependent tool call does not pay the cold model-load. Failure-soft.

    Triggered from the first ``tools/list`` (see ``_maybe_warm_embeddings``), NOT
    at startup. The model load imports the sentence-transformers stack
    (scipy/sklearn); on Python 3.14 importing that from a background thread that
    races the main thread's own startup imports deadlocks (observed via py-spy:
    the ``amem-embed-warmup`` thread wedged inside the scipy import). By the time
    tools/list runs, the main thread is parked in the stdio receive loop and no
    longer imports, so this background import is deadlock-free."""
    try:
        _runtime.provider().embed_batch(["warmup"])
    except Exception as exc:  # warm-up must never crash the server
        _log.debug("embed_warmup_failed", error=str(exc))


def _maybe_warm_embeddings(settings: Settings) -> threading.Thread | None:
    """Spawn the embedding warm-up daemon thread once, when enabled. Returns the
    thread (so tests can join it), or None when disabled or already started.

    Call this only after the server is serving (the main thread idle in the
    receive loop) -- it is invoked from ``_list_tools``. Spawning it earlier, in
    ``_run`` before ``_server.run``, raced server.run()'s imports and deadlocked
    the scipy/sklearn import on Python 3.14 (the first-call hang); importing the
    stack synchronously on the main thread instead blocked startup past the MCP
    client timeout so tools never registered. Deferring to the first tools/list
    fixes both: instant startup, deadlock-free background warm-up."""
    if not settings.mcp_warm_embed:
        return None
    if _warm_started.is_set():
        return None
    _warm_started.set()
    thread = threading.Thread(target=_warm_embeddings, name="amem-embed-warmup", daemon=True)
    thread.start()
    return thread


async def _run() -> None:
    settings = _runtime.settings
    configure_logging(settings.log_level)
    assert_local_only(settings)
    # Default HF to offline once the embedding model is cached -- before the
    # first tool handler triggers a lazy embedding load in this MCP process.
    maybe_configure_offline(settings)
    # NOTE: the embedding warm-up is intentionally NOT started here. Starting it
    # before _server.run() either deadlocks (background import racing
    # server.run's imports on Python 3.14) or blocks startup past the client
    # timeout (synchronous main-thread import). It is kicked off from the first
    # tools/list instead -- see _maybe_warm_embeddings / _list_tools.
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
