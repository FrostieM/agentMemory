"""v3 code indexer — one-file integration of chunks + symbol_edges + digest.

The discipline primitive ``memory_impact_check`` is only as useful as
the data in three tables:

  * ``code_digests``  — file-level summary (purpose, top symbols)
  * ``chunks``        — one row per top-level symbol (file_id link)
  * ``symbol_edges``  — calls / imports / extends / decorated_by

The v2 ingestion pipeline already knows how to populate the last two
via tree-sitter / Python AST.  This module wires that v2 plumbing into
the v3 bulk-index flow so ``impact_check`` can finally distinguish a
hub file from a leaf — hub files get ``verdict='medium'`` or ``'high'``
with concrete caller lists, leaves stay ``'low'``.

Architecture:

  index_file(conn, workspace_id, project_root, file_path, content)
    1.  Compute file_sha1; UPSERT into ``files`` (id by stable hash).
    2.  Chunk via ``chunking.chunk_code()``.
    3.  INSERT chunks rows; collect ``[(chunk_id, qualified_name), ...]``.
    4.  Call ``ingestion.file_persist_edges.persist_edges_for_file()``
        — runs the v2 extractor + resolver, writes ``symbol_edges``.
    5.  Compute digest via existing ``digest_worker.compute_digest()``;
        UPSERT into ``code_digests`` with updated edge counts.

Idempotency:  re-indexing the same file SHA is a no-op except for
``last_indexed_at`` bump.  Modified files DELETE prior chunks + edges
for that file_id (cascading) and re-insert.

Failure-soft:  any single-file error is logged and skipped; the
indexer never aborts.  Returns a per-file ``IndexResult`` so the bulk
caller can aggregate counts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.chunking.code import chunk_code
from agent_memory_lite.cognition.digest_worker import (
    compute_digest as _compute_digest,
)
from agent_memory_lite.ingestion.file_persist_edges import persist_edges_for_file

logger = logging.getLogger("agent_memory_lite.code_indexer")


@dataclass(slots=True)
class IndexResult:
    """Per-file outcome aggregated by the bulk caller."""

    file_path: str
    file_id: str = ""
    chunks: int = 0
    edges: int = 0
    digest_upserted: bool = False
    skipped_unchanged: bool = False
    error: str = ""
    languages: dict[str, int] = field(default_factory=dict)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace"), usedforsecurity=False).hexdigest()


def _stable_file_id(workspace_id: str, file_path: str) -> str:
    """Stable id from (workspace, relative path).  Re-runs hit the same row."""
    raw = f"{workspace_id}::{file_path}".encode()
    digest = hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:16]
    return f"file_{digest}"


def _upsert_file_row(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    workspace_id: str,
    rel_path: str,
    language: str | None,
    content_hash: str,
    size_bytes: int,
) -> None:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO files (id, workspace_id, path, language, content_hash,
                           size_bytes, last_indexed_at, metadata_json, is_archived)
        VALUES (?, ?, ?, ?, ?, ?, ?, '{}', 0)
        ON CONFLICT(workspace_id, path) DO UPDATE SET
            language=excluded.language,
            content_hash=excluded.content_hash,
            size_bytes=excluded.size_bytes,
            last_indexed_at=excluded.last_indexed_at
        """,
        (file_id, workspace_id, rel_path, language, content_hash, size_bytes, now),
    )


def _delete_prior_chunks_and_edges(
    conn: sqlite3.Connection, *, workspace_id: str, file_id: str
) -> None:
    """Wipe chunks + edges for one file before re-indexing.

    symbol_edges FK to chunks.id, so we delete edges first.  We DON'T
    cascade-drop the file row — chunks/edges churn but the file row's
    stable id stays so external references (search hits, audit log)
    don't go orphan.
    """
    # Edges where src OR dst is in this file's chunks.
    conn.execute(
        """
        DELETE FROM symbol_edges
        WHERE workspace_id = ?
          AND (src_chunk_id IN (SELECT id FROM chunks WHERE file_id = ?)
               OR dst_chunk_id IN (SELECT id FROM chunks WHERE file_id = ?))
        """,
        (workspace_id, file_id, file_id),
    )
    conn.execute(
        "DELETE FROM chunks WHERE workspace_id = ? AND file_id = ?",
        (workspace_id, file_id),
    )


def _insert_chunks(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_id: str,
    text: str,
    language: str | None,
) -> list[tuple[str, str | None]]:
    """Chunk the file + INSERT one row per chunk. Returns [(chunk_id, qname), ...]."""
    code_chunks = chunk_code(text, language=language)
    out: list[tuple[str, str | None]] = []
    now = _now_iso()
    for ck in code_chunks:
        chunk_id = f"chk_{uuid.uuid4().hex[:16]}"
        symbols_json = json.dumps(ck.symbols + (ck.extra_symbols or []))
        conn.execute(
            """
            INSERT INTO chunks (
                id, workspace_id, file_id, kind, text, gist,
                line_start, line_end, symbols_json, symbol_kind,
                qualified_name, parent_qualified_name, importance,
                confidence, is_archived, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.5, 0.5, 0, ?, '{}')
            """,
            (
                chunk_id,
                workspace_id,
                file_id,
                "symbol" if ck.qualified_name else "block",
                ck.text,
                ck.text[:200],
                ck.line_start,
                ck.line_end,
                symbols_json,
                ck.symbol_kind,
                ck.qualified_name,
                ck.parent_qualified_name,
                now,
            ),
        )
        out.append((chunk_id, ck.qualified_name))
    return out


def _upsert_digest_with_edge_counts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_id: str,
    rel_path: str,
    content: str,
    inbound: int,
    outbound: int,
) -> None:
    """Compute+UPSERT the code_digests row, including post-edge counts."""
    result = _compute_digest(rel_path, content)
    now = _now_iso()
    existing = conn.execute(
        "SELECT id FROM code_digests WHERE workspace_id = ? AND file_path = ?",
        (workspace_id, rel_path),
    ).fetchone()
    digest_id = existing[0] if existing else f"digest_{uuid.uuid4().hex[:16]}"
    conn.execute(
        """
        INSERT INTO code_digests (
            id, workspace_id, file_path, file_sha1, language,
            chunk_count, symbol_count, inbound_edge_count,
            outbound_edge_count, purpose_short, top_symbols_json,
            last_indexed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, file_path) DO UPDATE SET
            file_sha1=excluded.file_sha1,
            language=excluded.language,
            chunk_count=excluded.chunk_count,
            symbol_count=excluded.symbol_count,
            inbound_edge_count=excluded.inbound_edge_count,
            outbound_edge_count=excluded.outbound_edge_count,
            purpose_short=excluded.purpose_short,
            top_symbols_json=excluded.top_symbols_json,
            last_indexed_at=excluded.last_indexed_at,
            updated_at=excluded.updated_at
        """,
        (
            digest_id,
            workspace_id,
            rel_path,
            result.file_sha1,
            result.language,
            result.chunk_count,
            result.symbol_count,
            inbound,
            outbound,
            result.purpose_short,
            json.dumps(result.top_symbols, ensure_ascii=False),
            now,
            now,
        ),
    )


def _existing_file_sha(conn: sqlite3.Connection, *, workspace_id: str, rel_path: str) -> str | None:
    row = conn.execute(
        "SELECT content_hash FROM files WHERE workspace_id = ? AND path = ?",
        (workspace_id, rel_path),
    ).fetchone()
    return row[0] if row else None


def index_file(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    rel_path: str,
    content: str,
    language: str | None,
    force: bool = False,
) -> IndexResult:
    """Index one file:  files + chunks + edges + digest. Returns IndexResult.

    ``rel_path`` is the path stored in DB (typically project-relative).
    Idempotent — re-running on unchanged content is a no-op except for
    ``last_indexed_at`` bumps; ``force=True`` re-indexes regardless.
    """
    result = IndexResult(file_path=rel_path)
    content_hash = _sha1(content)
    if not force:
        prior = _existing_file_sha(conn, workspace_id=workspace_id, rel_path=rel_path)
        if prior == content_hash:
            result.skipped_unchanged = True
            return result
    file_id = _stable_file_id(workspace_id, rel_path)
    result.file_id = file_id
    try:
        _upsert_file_row(
            conn,
            file_id=file_id,
            workspace_id=workspace_id,
            rel_path=rel_path,
            language=language,
            content_hash=content_hash,
            size_bytes=len(content.encode("utf-8", errors="replace")),
        )
        _delete_prior_chunks_and_edges(conn, workspace_id=workspace_id, file_id=file_id)
        chunk_qnames = _insert_chunks(
            conn,
            workspace_id=workspace_id,
            file_id=file_id,
            text=content,
            language=language,
        )
        result.chunks = len(chunk_qnames)
        # Persist edges using v2 extractor + cross-file resolver.
        edges_written = persist_edges_for_file(
            conn,
            workspace_id=workspace_id,
            text=content,
            language=language,
            chunk_qnames=chunk_qnames,
        )
        result.edges = edges_written
        # Count this file's inbound + outbound for the digest row.
        inbound = conn.execute(
            """
            SELECT COUNT(*) FROM symbol_edges
            WHERE workspace_id = ? AND dst_chunk_id IN
                (SELECT id FROM chunks WHERE file_id = ?)
            """,
            (workspace_id, file_id),
        ).fetchone()[0]
        outbound = conn.execute(
            """
            SELECT COUNT(*) FROM symbol_edges
            WHERE workspace_id = ? AND src_chunk_id IN
                (SELECT id FROM chunks WHERE file_id = ?)
            """,
            (workspace_id, file_id),
        ).fetchone()[0]
        _upsert_digest_with_edge_counts(
            conn,
            workspace_id=workspace_id,
            file_id=file_id,
            rel_path=rel_path,
            content=content,
            inbound=int(inbound),
            outbound=int(outbound),
        )
        result.digest_upserted = True
    except (sqlite3.Error, ValueError, TypeError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        logger.warning("index_file_failed", extra={"file": rel_path, "error": result.error})
    return result


# ============================================================
# Cross-file resolver — second pass
# ============================================================


def resolve_all_pending_edges(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    """After bulk-indexing, run a final cross-file resolver pass.

    The per-file ``persist_edges_for_file`` calls already resolve any
    pending edges that *now* have a target in this file.  But files
    indexed earlier may still have NULL-dst edges whose targets only
    appeared in files indexed later.  This pass picks up those stragglers
    by re-running the resolver against every qname in the workspace.

    Returns the count of newly-resolved edges.
    """
    from agent_memory_lite.repositories.symbol_edges_resolver import (  # noqa: PLC0415
        resolve_pending_edges_for_qnames,
    )

    rows = conn.execute(
        "SELECT qualified_name, id FROM chunks WHERE workspace_id = ? AND qualified_name IS NOT NULL",
        (workspace_id,),
    ).fetchall()
    qname_to_chunk: dict[str, str] = {}
    for qname, chunk_id in rows:
        qname_to_chunk.setdefault(qname, chunk_id)
    if not qname_to_chunk:
        return 0
    before = conn.execute(
        "SELECT COUNT(*) FROM symbol_edges WHERE workspace_id = ? AND dst_chunk_id IS NULL",
        (workspace_id,),
    ).fetchone()[0]
    resolve_pending_edges_for_qnames(
        conn, workspace_id=workspace_id, qname_to_chunk_id=qname_to_chunk
    )
    after = conn.execute(
        "SELECT COUNT(*) FROM symbol_edges WHERE workspace_id = ? AND dst_chunk_id IS NULL",
        (workspace_id,),
    ).fetchone()[0]
    return int(before - after)


# ============================================================
# Edge-count refresh — after cross-file resolution
# ============================================================


def refresh_digest_edge_counts(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    """Update inbound_edge_count + outbound_edge_count on every digest row.

    Called after ``resolve_all_pending_edges`` so the impact_check
    verdicts reflect the final resolved graph, not the per-file snapshot
    taken during the first pass.

    Returns the number of digest rows updated.
    """
    rows = conn.execute(
        "SELECT file_path FROM code_digests WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    updated = 0
    for (rel_path,) in rows:
        file_id = _stable_file_id(workspace_id, str(rel_path))
        inbound = conn.execute(
            """
            SELECT COUNT(*) FROM symbol_edges
            WHERE workspace_id = ? AND dst_chunk_id IN
                (SELECT id FROM chunks WHERE file_id = ?)
            """,
            (workspace_id, file_id),
        ).fetchone()[0]
        outbound = conn.execute(
            """
            SELECT COUNT(*) FROM symbol_edges
            WHERE workspace_id = ? AND src_chunk_id IN
                (SELECT id FROM chunks WHERE file_id = ?)
            """,
            (workspace_id, file_id),
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE code_digests
            SET inbound_edge_count = ?, outbound_edge_count = ?,
                last_indexed_at = ?, updated_at = ?
            WHERE workspace_id = ? AND file_path = ?
            """,
            (inbound, outbound, _now_iso(), _now_iso(), workspace_id, rel_path),
        )
        updated += 1
    return updated


__all__: list[str] = [
    "IndexResult",
    "index_file",
    "refresh_digest_edge_counts",
    "resolve_all_pending_edges",
]


def _module_health() -> dict[str, Any]:
    """Diagnostic stub — kept for quick smoke checks."""
    return {"module": "v3.cognition.code_indexer", "version": 1}
