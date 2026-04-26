"""End-to-end write path for `memory_ingest_episode`.

Steps:
1. Redact secrets in the raw text.
2. Save the episode (with the redacted text as `raw_text`).
3. Create a single episode-kind chunk that mirrors the redacted text.
4. Insert the FTS row for that chunk.
5. Append an audit log entry.

Vector embedding is intentionally NOT computed here — that lands in Phase 2 when
the vector store is wired. The chunk record carries `embedding_id=None` until then.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.fts.chunks_fts import insert_chunk_fts
from agent_memory_lite.models.chunks import Chunk, ChunkIn
from agent_memory_lite.models.enums import ChunkKind
from agent_memory_lite.models.episodes import Episode, EpisodeIn
from agent_memory_lite.redaction import redact
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.chunks_repo import insert_chunk
from agent_memory_lite.repositories.episodes_repo import insert_episode


@dataclass(frozen=True, slots=True)
class EpisodeIngestResult:
    episode: Episode
    chunk: Chunk
    redacted_kinds: list[str]


def ingest_episode(
    conn: sqlite3.Connection,
    episode_in: EpisodeIn,
) -> EpisodeIngestResult:
    redacted = redact(episode_in.raw_text)

    with with_tx(conn):
        episode = insert_episode(conn, episode_in, redacted_text=redacted.text)

        chunk_in = ChunkIn(
            workspace_id=episode.workspace_id,
            episode_id=episode.id,
            kind=ChunkKind.EPISODE,
            text=redacted.text,
            summary=episode.summary,
            importance=episode.importance,
            confidence=episode.confidence,
            metadata={"source_type": episode.source_type.value},
        )
        chunk = insert_chunk(conn, chunk_in)

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
                "redacted_kinds": redacted.kinds_seen,
                "trust_level": episode.trust_level.value,
            },
        )

    return EpisodeIngestResult(
        episode=episode,
        chunk=chunk,
        redacted_kinds=redacted.kinds_seen,
    )
