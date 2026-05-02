"""Episode + retrieval + file MCP tool handlers."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.file_pipeline import ingest_file
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context
from agent_memory_lite.vector_store.base import VectorStore


def memory_get_context(
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


def memory_ingest_episode(
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
        "candidates_written": result.candidates_written,
    }


def memory_search(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str,
    limit: int = 10,
    **_kwargs: Any,
) -> dict[str, Any]:
    hits = search_chunks_fts(
        conn,
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


def memory_ingest_file(
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
