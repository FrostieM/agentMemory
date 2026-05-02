"""Ingest a single file into SQLite + FTS + (optional) vector store.

Idempotent: when the existing `files` row's `content_hash` matches the new
content, no chunks are written. Re-ingesting changed content drops the prior
chunks/FTS rows for the file before writing the new ones.

Chunking helpers live in ``file_chunking.py``; vector-side persistence
lives in ``file_persist.py``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.dimension_check import pin_or_check
from agent_memory_lite.fts.chunks_fts import insert_chunk_fts
from agent_memory_lite.ingestion.file_chunking import chunk_for_kind, chunk_kind_for
from agent_memory_lite.ingestion.file_persist import run_vector_phase
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.chunks import ChunkIn
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.files import FileRecord
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.chunks_repo import (
    delete_chunks_by_file,
    insert_chunk,
)
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.repositories.files_repo import get_file_by_path, upsert_file_row
from agent_memory_lite.utils.hashing import blake2b_hex
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore

_log = get_logger("ingestion.file_pipeline")


@dataclass(frozen=True, slots=True)
class FileIngestResult:
    file: FileRecord
    chunks_written: int
    skipped: bool


def ingest_file(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    path: str,
    content: str,
    language: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> FileIngestResult:
    timestamp = iso_now()
    content_hash = blake2b_hex(content)

    if embedding_provider is not None:
        pin_or_check(conn, workspace_id, embedding_provider)

    existing = get_file_by_path(conn, workspace_id=workspace_id, path=path)
    if existing is not None and existing.content_hash == content_hash:
        return FileIngestResult(file=existing, chunks_written=0, skipped=True)

    file_id = existing.id if existing is not None else new_id(IdKind.FILE)
    chunk_records = chunk_for_kind(content, language=language)
    kind = chunk_kind_for(language)

    new_chunk_ids: list[tuple[str, str, list[str]]] = []

    with with_tx(conn):
        upsert_file_row(
            conn,
            file_id=file_id,
            workspace_id=workspace_id,
            path=path,
            language=language,
            content_hash=content_hash,
            size_bytes=len(content.encode("utf-8")),
            metadata={"trust_level": TrustLevel.UNTRUSTED_DOC.value},
            timestamp=timestamp,
        )
        episode = insert_episode(
            conn,
            EpisodeIn(
                workspace_id=workspace_id,
                source_type=EpisodeSource.FILE_INDEXED,
                raw_text=f"file_indexed: {path}",
                trust_level=TrustLevel.AGENT_OBSERVED,
                importance=0.4,
                metadata={"path": path, "language": language},
            ),
        )

        if existing is not None:
            removed = delete_chunks_by_file(conn, file_id)
            if removed:
                # FTS sync — best-effort; the chunk_id list isn't easily
                # recoverable post-delete, so we rebuild the workspace's FTS
                # entries for this file by id below.
                conn.execute(
                    "DELETE FROM chunks_fts WHERE workspace_id = ? AND path = ?",
                    (workspace_id, path),
                )

        for text, line_start, line_end, symbols in chunk_records:
            if not text.strip():
                continue
            chunk = insert_chunk(
                conn,
                ChunkIn(
                    workspace_id=workspace_id,
                    file_id=file_id,
                    episode_id=episode.id,
                    kind=kind,
                    text=text,
                    line_start=line_start,
                    line_end=line_end,
                    symbols=symbols,
                    importance=0.4,
                    confidence=1.0,
                    metadata={"path": path, "language": language},
                ),
            )
            insert_chunk_fts(
                conn,
                chunk_id=chunk.id,
                workspace_id=workspace_id,
                path=path,
                symbols=symbols,
                text=chunk.text,
                summary=chunk.summary,
            )
            new_chunk_ids.append((chunk.id, chunk.text, symbols))

        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="ingest_file",
            target_type="file",
            target_id=file_id,
            source_episode_id=episode.id,
            after={"path": path, "chunks": len(new_chunk_ids)},
        )

    if embedding_provider is not None and vector_store is not None:
        run_vector_phase(
            conn,
            workspace_id=workspace_id,
            path=path,
            new_chunks=new_chunk_ids,
            provider=embedding_provider,
            store=vector_store,
        )

    file_record = get_file_by_path(conn, workspace_id=workspace_id, path=path)
    assert file_record is not None
    return FileIngestResult(file=file_record, chunks_written=len(new_chunk_ids), skipped=False)
