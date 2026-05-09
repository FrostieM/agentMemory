"""1.6.0: post-chunk persistence steps for the file ingest pipeline.

Split from ``file_pipeline.py`` to keep the orchestrator under the
SLOC ceiling. After every code chunk has been written + FTS-indexed,
this helper runs three follow-up passes:

1. Edge extraction + persistence (calls / imports / extends edges)
2. Cross-file edge resolver (link pending NULL-dst edges that target
   the chunks just written)
3. Symbol-version recording (append a row to ``symbol_versions``
   when content_hash differs from the most recent version per
   (workspace_id, qualified_name))

Returns the (edges_written, versions_written) counts so the caller
can include them in the audit log + response.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.file_digest_builder import build_digest
from agent_memory_lite.ingestion.file_persist_edges import persist_edges_for_file
from agent_memory_lite.ingestion.file_persist_similar_sigs import (
    record_similar_signature_edges,
)
from agent_memory_lite.ingestion.file_persist_soft_edges import record_co_change_pairs
from agent_memory_lite.ingestion.file_persist_versions import record_versions_for_chunks
from agent_memory_lite.models.chunks import Chunk
from agent_memory_lite.repositories.chunks_repo import (
    delete_chunks_by_file,
    list_chunk_ids_for_file,
)
from agent_memory_lite.repositories.file_digests_repo import upsert_digest
from agent_memory_lite.repositories.symbol_edges_repo import delete_edges_by_src_chunks
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class PostChunkCounts:
    edges_written: int
    versions_written: int
    soft_pairs_written: int
    digest_id: str | None


def run_pre_chunk_cleanup(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_id: str,
    path: str,
) -> None:
    """Drop dependent rows for a file that's about to be re-ingested.

    Order matters: edges reference chunks via src_chunk_id, so we
    drop edges first, then the FTS rows, then the chunks themselves.
    Symbol versions reference chunk_id but tolerate stale links —
    they survive re-ingest as historical records (intentional, that's
    how breaking-change detection works across versions).
    """
    old_chunk_ids = list_chunk_ids_for_file(conn, file_id)
    if old_chunk_ids:
        delete_edges_by_src_chunks(conn, workspace_id=workspace_id, chunk_ids=old_chunk_ids)
    removed = delete_chunks_by_file(conn, file_id)
    if removed:
        conn.execute(
            "DELETE FROM chunks_fts WHERE workspace_id = ? AND path = ?",
            (workspace_id, path),
        )


def _emit_similar_sigs(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    changed: list[tuple[str, str]],
    settings: Settings | None,
) -> None:
    for qn, sig in changed:
        record_similar_signature_edges(
            conn,
            workspace_id=workspace_id,
            new_qname=qn,
            new_signature=sig,
            settings=settings,
        )


def run_post_chunk_phase(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    text: str,
    language: str | None,
    file_path: str,
    new_chunks: list[Chunk],
    chunk_qnames: list[tuple[str, str | None]],
    settings: Settings | None = None,
) -> PostChunkCounts:
    edges_written = persist_edges_for_file(
        conn,
        workspace_id=workspace_id,
        text=text,
        language=language,
        chunk_qnames=chunk_qnames,
    )
    changed = record_versions_for_chunks(
        conn,
        workspace_id=workspace_id,
        chunks=new_chunks,
        file_path=file_path,
        language=language,
    )
    changed_qnames = [qn for qn, _sig in changed]
    soft_pairs_written = record_co_change_pairs(
        conn,
        workspace_id=workspace_id,
        changed_qnames=changed_qnames,
    )
    _emit_similar_sigs(conn, workspace_id=workspace_id, changed=changed, settings=settings)
    digest = upsert_digest(
        conn,
        build_digest(
            conn,
            workspace_id=workspace_id,
            file_path=file_path,
            language=language,
            chunks=new_chunks,
            last_indexed_at=iso_now(),
            settings=settings,
        ),
    )
    return PostChunkCounts(
        edges_written=edges_written,
        versions_written=len(changed_qnames),
        soft_pairs_written=soft_pairs_written,
        digest_id=digest.id,
    )
