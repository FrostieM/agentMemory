"""MCP stdio server.

Exposes the same service functions used by the HTTP routes as MCP tools so
Claude Code, Cursor, or any other MCP-aware agent can discover them as
first-class tool calls without needing the HTTP service to be up.

Run via:

    python -m agent_memory_lite.mcp.stdio_server

Register in Claude Code (`~/.claude/settings.json` or project
`.claude/settings.json`):

    {
      "mcpServers": {
        "agent-memory-lite": {
          "command": "python",
          "args": ["-m", "agent_memory_lite.mcp.stdio_server"],
          "env": {"OLLAMA_PROBE_SKIP": "true"}
        }
      }
    }

The server runs in-process: it owns one SQLite connection, lazy-loads the
embedding provider on first use, and shares the same SQLite database the
HTTP service uses (LanceDB likewise). Multiple processes hitting the same
DB are safe under SQLite WAL.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import mcp.server.stdio
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.file_pipeline import ingest_file
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.logging_setup import configure_logging, get_logger
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.models.task_state import TaskStateIn
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.factory import get_vector_store
from agent_memory_lite.version import __version__

_log = get_logger("mcp.stdio_server")


def _resolve_paths_from_cwd(settings: Settings) -> Settings:
    """Override settings.db_path / settings.vector_db_path from the cwd.

    Precedence (highest first):
    1. Explicit env vars `MEMORY_DB_PATH` / `VECTOR_DB_PATH` (already
       baked into Settings via pydantic-settings).
    2. `<cwd>/.agent_memory/memory.db` if present — lets any runtime
       isolate per-project memory by spawning the MCP server in the
       project's working directory.
    3. Whatever the .env / built-in default produced.
    """
    if os.environ.get("MEMORY_DB_PATH"):
        return settings
    cwd = Path.cwd()
    candidate_db = cwd / ".agent_memory" / "memory.db"
    candidate_vec = cwd / ".agent_memory" / "vectors.lance"
    if not candidate_db.parent.exists():
        return settings
    return settings.model_copy(
        update={"db_path": candidate_db, "vector_db_path": candidate_vec}
    )


class _Runtime:
    """Lazy holders for the per-process SQLite + provider + store."""

    def __init__(self) -> None:
        self.settings = _resolve_paths_from_cwd(get_settings())
        self.conn: sqlite3.Connection | None = None
        self._provider: EmbeddingProvider | None = None
        self._store: VectorStore | None = None

    def db(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = open_connection(self.settings.db_path)
            apply_migrations(self.conn)
        return self.conn

    def provider(self) -> EmbeddingProvider:
        if self._provider is None:
            self._provider = get_embedding_provider(self.settings)
        return self._provider

    def store(self) -> VectorStore:
        if self._store is None:
            self._store = get_vector_store(self.settings)
        return self._store

    def close(self) -> None:
        if self.conn is not None:
            close_connection(self.conn)
            self.conn = None
        if self._store is not None:
            self._store.close()
            self._store = None


_runtime = _Runtime()
_server: Server = Server("agent-memory-lite")


_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_get_context",
        description=(
            "Retrieve the agent's memory context for the given query. Returns an "
            "XML envelope with core_memory, task_state, active_decisions, "
            "procedural_rules, retrieved_facts, and retrieved_chunks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "default": "default"},
                "task_id": {"type": "string"},
                "query": {"type": "string", "minLength": 1},
                "files_in_scope": {"type": "array", "items": {"type": "string"}},
                "max_tokens": {
                    "type": "integer",
                    "minimum": 200,
                    "maximum": 32000,
                    "default": 3500,
                },
                "historical": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="memory_search",
        description="Exact FTS lookup over chunks (BM25 ordered).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "default": "default"},
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 200},
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="memory_ingest_episode",
        description=(
            "Persist an event into episodic memory. Secrets are redacted server "
            "side before storage, embedding, and FTS indexing."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "default": "default"},
                "session_id": {"type": "string"},
                "task_id": {"type": "string"},
                "source_type": {"type": "string", "default": "agent_action"},
                "raw_text": {"type": "string", "minLength": 1},
                "trust_level": {"type": "string", "default": "agent_observed"},
                "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5},
            },
            "required": ["raw_text"],
        },
    ),
    types.Tool(
        name="memory_write_decision",
        description=(
            "Record an architectural decision. Pass supersedes_decision_id to "
            "close a prior decision atomically."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "default": "default"},
                "title": {"type": "string", "minLength": 1},
                "decision_text": {"type": "string", "minLength": 1},
                "rationale": {"type": "string"},
                "supersedes_decision_id": {"type": "string"},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.9},
                "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.8},
            },
            "required": ["title", "decision_text"],
        },
    ),
    types.Tool(
        name="memory_update_task_state",
        description="Upsert task state keyed by (workspace_id, task_id).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "default": "default"},
                "task_id": {"type": "string", "minLength": 1},
                "goal": {"type": "string", "minLength": 1},
                "status": {"type": "string", "minLength": 1},
                "current_plan": {"type": "array", "items": {"type": "string"}},
                "completed_steps": {"type": "array", "items": {"type": "string"}},
                "next_action": {"type": "string"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "files_in_scope": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
            },
            "required": ["task_id", "goal", "status"],
        },
    ),
    types.Tool(
        name="memory_ingest_file",
        description="Index a single file (idempotent by content_hash).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "default": "default"},
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "language": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ),
]


@_server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
async def _list_tools() -> list[types.Tool]:
    return _TOOLS


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _handle_get_context(args: dict[str, Any]) -> dict[str, Any]:
    query = RetrievalQuery(**_drop_none(args))
    built = build_context(
        _runtime.db(),
        query,
        embedding_provider=_runtime.provider(),
        vector_store=_runtime.store(),
    )
    return {
        "context_text": built.text,
        "sources": [
            {
                "id": hit.id,
                "score": hit.score,
                "sources": hit.sources,
                "path": hit.path,
            }
            for hit in built.hits
        ],
    }


def _handle_search(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id", "default")
    query = args["query"]
    limit = int(args.get("limit", 10))
    hits = search_chunks_fts(
        _runtime.db(),
        workspace_id=workspace_id,
        query=query,
        limit=limit,
    )
    return {
        "mode": "fts",
        "hits": [
            {
                "chunk_id": hit.chunk_id,
                "score": hit.score,
                "path": hit.path,
                "text": hit.text,
                "summary": hit.summary,
            }
            for hit in hits
        ],
    }


def _handle_ingest_episode(args: dict[str, Any]) -> dict[str, Any]:
    payload = _drop_none(args)
    payload.setdefault("source_type", "agent_action")
    payload.setdefault("trust_level", "agent_observed")
    result = ingest_episode(
        _runtime.db(),
        EpisodeIn(**payload),
        embedding_provider=_runtime.provider(),
        vector_store=_runtime.store(),
        auto_promote_settings=_runtime.settings,
    )
    return {
        "episode_id": result.episode.id,
        "chunk_id": result.chunk.id,
        "redacted_text": result.episode.raw_text,
        "redacted_kinds": result.redacted_kinds,
        "embedded": result.embedded,
        "auto_promoted_decisions": result.auto_promoted_decisions,
        "auto_promoted_rules": result.auto_promoted_rules,
        "auto_promoted_core": result.auto_promoted_core,
    }


def _handle_write_decision(args: dict[str, Any]) -> dict[str, Any]:
    decision = write_decision(_runtime.db(), DecisionIn(**_drop_none(args)))
    return {
        "decision_id": decision.id,
        "status": decision.status.value,
        "valid_from": decision.valid_from,
        "superseded_decision_id": decision.supersedes_decision_id,
    }


def _handle_update_task_state(args: dict[str, Any]) -> dict[str, Any]:
    state = write_task_state(_runtime.db(), TaskStateIn(**_drop_none(args)))
    return {
        "state_id": state.id,
        "task_id": state.task_id,
        "status": state.status,
        "updated_at": state.updated_at,
    }


def _handle_ingest_file(args: dict[str, Any]) -> dict[str, Any]:
    payload = _drop_none(args)
    workspace_id = payload.pop("workspace_id", "default")
    result = ingest_file(
        _runtime.db(),
        workspace_id=workspace_id,
        embedding_provider=_runtime.provider(),
        vector_store=_runtime.store(),
        **payload,
    )
    return {
        "file_id": result.file.id,
        "path": result.file.path,
        "chunks_written": result.chunks_written,
        "skipped": result.skipped,
        "last_indexed_at": result.file.last_indexed_at,
    }


_HANDLERS = {
    "memory_get_context": _handle_get_context,
    "memory_search": _handle_search,
    "memory_ingest_episode": _handle_ingest_episode,
    "memory_write_decision": _handle_write_decision,
    "memory_update_task_state": _handle_update_task_state,
    "memory_ingest_file": _handle_ingest_file,
}


@_server.call_tool()  # type: ignore[untyped-decorator]
async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    if name not in _HANDLERS:
        return [types.TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}))]
    args = arguments or {}
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
