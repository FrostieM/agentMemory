"""Tool definitions exposed via MCP.

Each tool maps to the same service function used by the HTTP routes. The
JSON schema is intentionally loose; the underlying functions own validation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.file_pipeline import ingest_file
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.models.task_state import TaskStateIn
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.vector_store.base import VectorStore

ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    handler: ToolHandler


def _memory_get_context(
    *,
    conn: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    workspace_id: str = "default",
    query: str,
    task_id: str | None = None,
    max_tokens: int = 3500,
    historical: bool = False,
) -> dict[str, Any]:
    built = build_context(
        conn,
        RetrievalQuery(
            workspace_id=workspace_id,
            query=query,
            task_id=task_id,
            max_tokens=max_tokens,
            historical=historical,
        ),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    return {
        "context_text": built.text,
        "sources": [
            {"id": hit.id, "score": hit.score, "sources": hit.sources} for hit in built.hits
        ],
    }


def _memory_ingest_episode(
    *,
    conn: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = ingest_episode(
        conn,
        EpisodeIn(**payload),
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    return {
        "episode_id": result.episode.id,
        "chunk_id": result.chunk.id,
        "redacted_text": result.episode.raw_text,
        "redacted_kinds": result.redacted_kinds,
    }


def _memory_ingest_file(
    *,
    conn: sqlite3.Connection,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = ingest_file(
        conn,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        **payload,
    )
    return {
        "file_id": result.file.id,
        "path": result.file.path,
        "chunks_written": result.chunks_written,
        "skipped": result.skipped,
    }


def _memory_write_decision(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    decision = write_decision(conn, DecisionIn(**payload))
    return {"decision_id": decision.id, "status": decision.status.value}


def _memory_update_task_state(
    *,
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    **_kwargs: Any,
) -> dict[str, Any]:
    state = write_task_state(conn, TaskStateIn(**payload))
    return {"state_id": state.id, "task_id": state.task_id, "status": state.status}


TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="memory_get_context",
        description="Retrieve memory context for the agent before a task.",
        handler=_memory_get_context,
    ),
    ToolDefinition(
        name="memory_ingest_episode",
        description="Persist an event into episodic memory with redaction.",
        handler=_memory_ingest_episode,
    ),
    ToolDefinition(
        name="memory_ingest_file",
        description="Index a single file into memory, idempotent by content hash.",
        handler=_memory_ingest_file,
    ),
    ToolDefinition(
        name="memory_write_decision",
        description="Record an architectural decision; supports supersedes chains.",
        handler=_memory_write_decision,
    ),
    ToolDefinition(
        name="memory_update_task_state",
        description="Upsert task state for (workspace_id, task_id).",
        handler=_memory_update_task_state,
    ),
)


def dispatch(name: str, **kwargs: Any) -> dict[str, Any]:
    for tool in TOOLS:
        if tool.name == name:
            return tool.handler(**kwargs)
    raise KeyError(f"unknown MCP tool: {name!r}")
