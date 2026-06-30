"""Persistence helpers for the episode ingest pipeline.

Split out of ``episode_pipeline.py`` so the orchestrator stays under
the SLOC ceiling. ``persist`` runs the SQLite-side write inside one
transaction; ``embed_and_upsert`` runs the optional vector-side write
after the transaction commits.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.fts.chunks_fts import insert_chunk_fts
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.chunks import Chunk, ChunkIn
from agent_memory_lite.models.enums import ChunkKind
from agent_memory_lite.models.episodes import Episode, EpisodeIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.chunks_repo import (
    EMBEDDING_TEXT_SHA256_KEY,
    chunk_text_sha256,
    insert_chunk,
)
from agent_memory_lite.repositories.episodes_extraction_repo import (
    mark_episode_extraction_pending,
)
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.vector_store.base import VectorRow, VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

_log = get_logger("ingestion.episode_persist")


def persist(
    conn: sqlite3.Connection,
    episode_in: EpisodeIn,
    redacted_text: str,
    redacted_kinds: list[str],
    *,
    extraction_pending: bool = False,
) -> tuple[Episode, Chunk]:
    """Persist the episode + chunk + FTS row + audit in ONE transaction.

    ``extraction_pending`` (Batch E async write): set the episode's
    extraction_pending flag IN THIS SAME transaction so "episode exists" and
    "episode awaits deferred extraction" are atomic -- there is no window where a
    persisted episode is silently un-flagged. The brain-pass extraction-drain step
    later runs the extractor and clears it.
    """
    with with_tx(conn):
        episode = insert_episode(conn, episode_in, redacted_text=redacted_text)
        chunk = insert_chunk(
            conn,
            ChunkIn(
                workspace_id=episode.workspace_id,
                episode_id=episode.id,
                kind=ChunkKind.EPISODE,
                text=redacted_text,
                summary=episode.summary,
                # Mirror the episode's visual label onto its primary chunk
                # so the live observatory UI can render either consistently.
                label=episode.label,
                importance=episode.importance,
                confidence=episode.confidence,
                metadata={"source_type": episode.source_type.value},
            ),
        )
        insert_chunk_fts(
            conn,
            chunk_id=chunk.id,
            workspace_id=chunk.workspace_id,
            path=None,
            symbols=[],
            text=chunk.text,
            summary=chunk.summary,
        )
        insert_audit(
            conn,
            workspace_id=episode.workspace_id,
            action="ingest_episode",
            target_type="episode",
            target_id=episode.id,
            source_episode_id=episode.id,
            after={
                "chunk_id": chunk.id,
                "redacted_kinds": redacted_kinds,
                "trust_level": episode.trust_level.value,
            },
        )
        if extraction_pending:
            mark_episode_extraction_pending(conn, episode.id)
    return episode, chunk


def embed_and_upsert(
    chunk: Chunk,
    text: str,
    provider: EmbeddingProvider,
    store: VectorStore,
) -> bool:
    try:
        vectors = provider.embed_batch([text], kind="doc")
        store.upsert(
            NAMESPACE_CHUNKS,
            [
                VectorRow(
                    id=chunk.id,
                    workspace_id=chunk.workspace_id,
                    vector=vectors[0],
                    metadata={
                        "chunk_id": chunk.id,
                        "kind": chunk.kind.value,
                        "episode_id": chunk.episode_id,
                        "path": None,
                        EMBEDDING_TEXT_SHA256_KEY: chunk_text_sha256(text),
                    },
                )
            ],
        )
    except Exception as exc:
        _log.warning("vector_upsert_failed", chunk_id=chunk.id, error=str(exc))
        return False
    return True
