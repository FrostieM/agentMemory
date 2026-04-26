"""Vector candidate fetcher.

Embeds the query text once and asks the vector store for nearest neighbours.
The store returns a `score` already in similarity space (higher = closer).
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.repositories.chunks_repo import get_chunk
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

DEFAULT_LIMIT = 30


def collect_vector(
    conn: sqlite3.Connection,
    store: VectorStore,
    provider: EmbeddingProvider,
    *,
    workspace_id: str,
    query: str,
    limit: int = DEFAULT_LIMIT,
) -> list[RetrievalCandidate]:
    if not query.strip():
        return []
    vectors = provider.embed_batch([query], kind="query")
    hits = store.query(
        NAMESPACE_CHUNKS,
        vectors[0],
        workspace_id=workspace_id,
        k=limit,
    )
    candidates: list[RetrievalCandidate] = []
    for hit in hits:
        chunk = get_chunk(conn, hit.id)
        if chunk is None:
            continue
        candidates.append(
            RetrievalCandidate(
                id=chunk.id,
                workspace_id=chunk.workspace_id,
                source="vector",
                text=chunk.text,
                path=str(hit.metadata.get("path") or ""),
                summary=chunk.summary,
                raw_score=hit.score,
                metadata={"kind": chunk.kind.value, "episode_id": chunk.episode_id},
            )
        )
    return candidates
