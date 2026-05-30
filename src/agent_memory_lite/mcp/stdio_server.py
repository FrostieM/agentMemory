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
    "_preimport_embedding_stack",
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


def _warm_embeddings() -> None:
    """Best-effort: build the embedding model now so the first tool call does
    not pay the cold model-build. Runs in a daemon thread; failure-soft.

    The heavy *import* of the sentence-transformers stack is forced onto the
    main thread first (see ``_preimport_embedding_stack``), so this daemon
    thread only triggers the model *build*, never the deadlock-prone import."""
    try:
        _runtime.provider().embed_batch(["warmup"])
    except Exception as exc:  # warm-up must never crash the server
        _log.debug("embed_warmup_failed", error=str(exc))


def _preimport_embedding_stack(settings: Settings) -> None:
    """Import the sentence-transformers stack on the CURRENT (main) thread.

    ``import sentence_transformers`` transitively pulls ``scipy.stats`` and
    ``sklearn`` -- a large native-extension tree. On Python 3.14, importing it
    from the background warm-up *daemon* thread can wedge on the import lock and
    never finish (observed via py-spy: the ``amem-embed-warmup`` thread idle
    inside the scipy import). Because ``SentenceTransformersProvider._load``
    holds ``_load_lock`` across ``_build_model``, a wedged warm-up holds that
    lock forever and every embedding-dependent tool call (writes, rerank) hangs
    until the MCP client times out -- the "first call hangs for minutes" bug.

    Importing here -- on the main thread, before the warm-up thread or any
    ``asyncio.to_thread`` handler can race it -- makes every later import a
    cache hit. Failure-soft: a missing/broken stack must not stop the server
    starting; the provider raises a clean error on first use instead.
    """
    if settings.embedding_backend != "sentence_transformers":
        return
    try:
        import sentence_transformers  # noqa: F401, PLC0415
    except Exception as exc:  # pragma: no cover - install/env breakage only
        _log.debug("preimport_embedding_stack_failed", error=str(exc))


def _maybe_warm_embeddings(settings: Settings) -> threading.Thread | None:
    """Spawn the background embedding warm-up when enabled. Returns the thread
    (so tests can join it) or None when disabled. The heavy embedding import is
    forced onto this (main) thread first so the daemon thread cannot deadlock on
    it -- see ``_preimport_embedding_stack``."""
    if not settings.mcp_warm_embed:
        return None
    _preimport_embedding_stack(settings)
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
    # Warm the embedding model off the critical path so the first tool call
    # that needs an embedding does not pay the cold model-load.
    _maybe_warm_embeddings(settings)
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
