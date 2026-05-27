"""Vector candidate fetcher.

Embeds the query text once and asks the vector store for nearest neighbours.
The store returns a `score` already in similarity space (higher = closer).
"""

from __future__ import annotations

import logging
import sqlite3

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.repositories.chunks_repo import get_chunk
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

DEFAULT_LIMIT = 30
_LOG = logging.getLogger(__name__)


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
    # v3.5 sector-2 audit-followup: tolerate embedder + vector-store
    # failures so a corrupt LanceDB table / Ollama OOM / model cold-start
    # error cannot 500 the entire compact read path. Retrieval
    # degrades gracefully to FTS-only when this returns ``[]``.
    try:
        vectors = provider.embed_batch([query], kind="query")
    except Exception as exc:  # every embed failure must degrade to FTS-only, not crash
        _LOG.warning("collect_vector: embed_batch failed (%s); degrading to FTS-only", exc)
        return []
    # ``vectors`` is np.ndarray of shape (N, dim). Use len() — ``not vectors``
    # is ambiguous on a numpy array (raises "truth value of array").
    if len(vectors) == 0:
        return []
    try:
        hits = store.query(
            NAMESPACE_CHUNKS,
            vectors[0],
            workspace_id=workspace_id,
            k=limit,
        )
    except Exception as exc:  # same degrade-not-crash rationale as the embed call above
        _LOG.warning("collect_vector: store.query failed (%s); degrading to FTS-only", exc)
        return []
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
                metadata={
                    "kind": chunk.kind.value,
                    "episode_id": chunk.episode_id,
                    "created_at": chunk.created_at,
                    "importance": chunk.importance,
                    "confidence": chunk.confidence,
                    "vector_score": hit.score,
                },
            )
        )
    return candidates
