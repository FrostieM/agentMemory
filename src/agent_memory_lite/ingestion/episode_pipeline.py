"""End-to-end write path for `memory_ingest_episode`.

Steps:
1. Redact secrets in the raw text.
2. Save the episode (with the redacted text as `raw_text`).
3. Create a single episode-kind chunk that mirrors the redacted text.
4. Insert the FTS row for that chunk.
5. Append an audit log entry.
6. (Optional) Embed the chunk and upsert into the vector store.

When `embedding_provider` is supplied, dimension drift is checked first via
`pin_or_check`. Vector upsert happens *after* the SQLite transaction commits;
a vector-store failure logs a warning but does not roll the chunk back. Run
`scripts/reindex_vectors.py` to repair drift.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.dimension_check import pin_or_check
from agent_memory_lite.fts.chunks_fts import insert_chunk_fts
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.chunks import Chunk, ChunkIn
from agent_memory_lite.models.enums import ChunkKind
from agent_memory_lite.models.episodes import Episode, EpisodeIn
from agent_memory_lite.redaction import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.chunks_repo import insert_chunk
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.vector_store.base import VectorRow, VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

_log = get_logger("ingestion.episode_pipeline")


@dataclass(frozen=True, slots=True)
class EpisodeIngestResult:
    episode: Episode
    chunk: Chunk
    redacted_kinds: list[str]
    embedded: bool
    auto_promoted_decisions: int = 0
    auto_promoted_rules: int = 0
    auto_promoted_core: int = 0


def _persist(
    conn: sqlite3.Connection, episode_in: EpisodeIn, redacted_text: str, redacted_kinds: list[str]
) -> tuple[Episode, Chunk]:
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
    return episode, chunk


def _embed_and_upsert(
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
                    },
                )
            ],
        )
    except Exception as exc:
        _log.warning(
            "vector_upsert_failed",
            chunk_id=chunk.id,
            error=str(exc),
        )
        return False
    return True


def ingest_episode(
    conn: sqlite3.Connection,
    episode_in: EpisodeIn,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    auto_promote_settings: Settings | None = None,
) -> EpisodeIngestResult:
    redacted = redact(episode_in.raw_text)

    if embedding_provider is not None:
        pin_or_check(conn, episode_in.workspace_id, embedding_provider)

    episode, chunk = _persist(conn, episode_in, redacted.text, redacted.kinds_seen)

    embedded = False
    if embedding_provider is not None and vector_store is not None:
        embedded = _embed_and_upsert(chunk, redacted.text, embedding_provider, vector_store)

    decisions = rules = core = 0
    if auto_promote_settings is not None:
        from agent_memory_lite.ingestion.auto_promote import auto_promote  # noqa: PLC0415

        try:
            stats = auto_promote(conn, episode, auto_promote_settings)
            decisions = stats.decisions_written
            rules = stats.rules_written
            core = stats.core_written
        except Exception as exc:
            _log.warning("auto_promote_failed", episode_id=episode.id, error=str(exc))

    return EpisodeIngestResult(
        episode=episode,
        chunk=chunk,
        redacted_kinds=redacted.kinds_seen,
        embedded=embedded,
        auto_promoted_decisions=decisions,
        auto_promoted_rules=rules,
        auto_promoted_core=core,
    )
