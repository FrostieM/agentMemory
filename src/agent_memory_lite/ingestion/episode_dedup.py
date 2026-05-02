"""Embedding-based deduplication for incoming episodes.

Agents tend to ingest near-identical episode text repeatedly ("re-ran
the test", "running tests again"). Without dedup the workspace fills
with low-information rows and ``get_context`` keeps surfacing the
same content under slightly different keywords.

When ``MEMORY_EPISODE_DEDUP_ENABLED=1``, ``ingest_episode`` runs the
new redacted text through the embedding provider once, queries the
chunk-vector store for the top-k nearest neighbours in the same
workspace, and if the highest cosine similarity is at or above the
configured threshold, returns the existing episode_id instead of
inserting. The original chunk wins; the duplicate ingest is
idempotent.

This module is opt-in by env. With the flag off ``maybe_dedup``
short-circuits to ``None`` and the pipeline behaves exactly as
before.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

_log = get_logger("ingestion.episode_dedup")


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    chunk_id: str
    score: float
    episode_id: str | None


def _episode_id_for_chunk(conn: sqlite3.Connection, chunk_id: str) -> str | None:
    row = conn.execute(
        "SELECT episode_id FROM chunks WHERE id = ?",
        (chunk_id,),
    ).fetchone()
    if row is None:
        return None
    value = row["episode_id"] if "episode_id" in row else row[0]
    return str(value) if value else None


def _embed_first(
    workspace_id: str,
    text: str,
    embedding_provider: EmbeddingProvider,
) -> np.ndarray | None:
    try:
        vector = embedding_provider.embed_batch([text], kind="doc")
    except Exception as exc:
        _log.warning("dedup_embed_failed", workspace_id=workspace_id, error=str(exc))
        return None
    if vector is None or len(vector) == 0:
        return None
    first = vector[0]
    return first if isinstance(first, np.ndarray) else np.asarray(first)


def maybe_dedup(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    redacted_text: str,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
    settings: Settings | None,
) -> DuplicateMatch | None:
    """Return a DuplicateMatch when the new text near-matches an
    existing chunk in the same workspace; otherwise None."""
    if (
        settings is None
        or not settings.episode_dedup_enabled
        or embedding_provider is None
        or vector_store is None
        or not redacted_text
        or not redacted_text.strip()
    ):
        return None
    vector = _embed_first(workspace_id, redacted_text, embedding_provider)
    if vector is None:
        return None
    try:
        hits = vector_store.query(
            NAMESPACE_CHUNKS,
            vector,
            workspace_id=workspace_id,
            k=min(settings.episode_dedup_window, 10),
        )
    except Exception as exc:
        _log.warning("dedup_vector_query_failed", workspace_id=workspace_id, error=str(exc))
        return None
    if not hits:
        return None
    best = hits[0]
    if best.score < settings.episode_dedup_threshold:
        return None
    episode_id = _episode_id_for_chunk(conn, best.id)
    return DuplicateMatch(chunk_id=best.id, score=float(best.score), episode_id=episode_id)
