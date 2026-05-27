"""Ingest a single file into SQLite + FTS + (optional) vector store.

Idempotent: when the existing `files` row's `content_hash` matches the new
content, no chunks are written. Re-ingesting changed content drops the prior
chunks/FTS rows for the file before writing the new ones.

Chunking helpers live in ``file_chunking.py``; vector-side persistence
lives in ``file_persist.py``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.dimension_check import pin_or_check
from agent_memory_lite.ingestion.file_audit import record_file_ingest_audit
from agent_memory_lite.ingestion.file_chunking import chunk_for_kind, chunk_kind_for
from agent_memory_lite.ingestion.file_chunks_loop import persist_chunks_loop
from agent_memory_lite.ingestion.file_existing import refresh_existing_file_metadata
from agent_memory_lite.ingestion.file_persist import run_vector_phase
from agent_memory_lite.ingestion.file_post_chunk import (
    run_post_chunk_phase,
    run_pre_chunk_cleanup,
)
from agent_memory_lite.ingestion.file_result import FileIngestResult
from agent_memory_lite.models.enums import EpisodeSource, TrustLevel
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.redaction.redactor import redact
from agent_memory_lite.repositories.episodes_repo import insert_episode
from agent_memory_lite.repositories.files_repo import get_file_by_path, upsert_file_row
from agent_memory_lite.utils.hashing import blake2b_hex
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore


def ingest_file(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    path: str,
    content: str,
    source_bytes: bytes | None = None,
    language: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    settings: Settings | None = None,
) -> FileIngestResult:
    timestamp = iso_now()
    hash_source = source_bytes if source_bytes is not None else content
    size_bytes = len(source_bytes) if source_bytes is not None else len(content.encode("utf-8"))
    content_hash = blake2b_hex(hash_source)
    if embedding_provider is not None:
        pin_or_check(conn, workspace_id, embedding_provider)

    existing = get_file_by_path(conn, workspace_id=workspace_id, path=path)
    if existing is not None and existing.content_hash == content_hash:
        if existing.size_bytes != size_bytes or existing.language != language:
            existing = refresh_existing_file_metadata(
                conn,
                existing=existing,
                workspace_id=workspace_id,
                path=path,
                language=language,
                content_hash=content_hash,
                size_bytes=size_bytes,
                timestamp=timestamp,
            )
        return FileIngestResult(
            file=existing,
            chunks_written=0,
            edges_written=0,
            versions_written=0,
            skipped=True,
        )

    file_id = existing.id if existing is not None else new_id(IdKind.FILE)
    # v3.5 audit-followup: secret leakage fix. /memory/ingest_file
    # stamps trust_level=UNTRUSTED_DOC and writes file contents straight
    # into chunks.text + chunks_fts. A file containing API keys,
    # ``.env``-style assignments, or JWTs would have landed in plaintext.
    # Redact the body BEFORE chunking so secrets never reach the chunker.
    # When the caller has source bytes, content_hash and size_bytes track
    # the exact file on disk; redaction only affects chunk text.
    redacted = redact(content)
    content = redacted.text
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
            size_bytes=size_bytes,
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
        record_file_ingest_audit(
            conn,
            workspace_id=workspace_id,
            file_id=file_id,
            episode_id=episode.id,
            path=path,
            chunks=len(new_chunk_ids),
            edges=post.edges_written,
            versions=post.versions_written,
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
