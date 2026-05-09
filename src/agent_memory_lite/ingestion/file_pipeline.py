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

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.dimension_check import pin_or_check
from agent_memory_lite.ingestion.file_chunking import chunk_for_kind, chunk_kind_for
from agent_memory_lite.ingestion.file_persist import run_vector_phase
from agent_memory_lite.ingestion.file_post_chunk import (
    persist_chunks_loop,
    run_post_chunk_phase,
    run_pre_chunk_cleanup,
)
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.files import FileRecord
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.repositories.files_repo import get_file_by_path, upsert_file_row
from agent_memory_lite.utils.hashing import blake2b_hex
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore


@dataclass(frozen=True, slots=True)
class FileIngestResult:
    file: FileRecord
    chunks_written: int
    edges_written: int
    versions_written: int
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
    settings: Settings | None = None,
) -> FileIngestResult:
    timestamp = iso_now()
    content_hash = blake2b_hex(content)
    if embedding_provider is not None:
        pin_or_check(conn, workspace_id, embedding_provider)

    existing = get_file_by_path(conn, workspace_id=workspace_id, path=path)
    if existing is not None and existing.content_hash == content_hash:
        return FileIngestResult(
            file=existing,
            chunks_written=0,
            edges_written=0,
            versions_written=0,
            skipped=True,
        )

    file_id = existing.id if existing is not None else new_id(IdKind.FILE)
    chunk_records = chunk_for_kind(content, language=language)
    kind = chunk_kind_for(language)
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
            run_pre_chunk_cleanup(conn, workspace_id=workspace_id, file_id=file_id, path=path)
        new_chunk_ids, new_chunk_qnames, new_chunks_full = persist_chunks_loop(
            conn,
            workspace_id=workspace_id,
            file_id=file_id,
            episode_id=episode.id,
            kind=kind,
            records=chunk_records,
            path=path,
            language=language,
        )
        post = run_post_chunk_phase(
            conn,
            workspace_id=workspace_id,
            text=content,
            language=language,
            file_path=path,
            new_chunks=new_chunks_full,
            chunk_qnames=new_chunk_qnames,
            settings=settings,
        )
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="ingest_file",
            target_type="file",
            target_id=file_id,
            source_episode_id=episode.id,
            after={
                "path": path,
                "chunks": len(new_chunk_ids),
                "edges": post.edges_written,
                "versions": post.versions_written,
            },
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
    return FileIngestResult(
        file=file_record,
        chunks_written=len(new_chunk_ids),
        edges_written=post.edges_written,
        versions_written=post.versions_written,
        skipped=False,
    )
