"""FTS-side integrity checks + retrieval round-trip check."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.maintenance.integrity_models import (
    IntegrityCheck,
    count_query,
    sample_query,
    table_exists,
)
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder import build_context


def fts_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not table_exists(conn, "chunks_fts"):
        return IntegrityCheck(status="degraded", details={"error": "chunks_fts missing"})
    chunk_count = count_query(
        conn, "SELECT COUNT(*) FROM chunks WHERE workspace_id = ?", (workspace_id,)
    )
    fts_count = count_query(
        conn,
        "SELECT COUNT(*) FROM chunks_fts WHERE workspace_id = ?",
        (workspace_id,),
    )
    missing = count_query(
        conn,
        """
        SELECT COUNT(*)
        FROM chunks c
        LEFT JOIN chunks_fts f ON f.chunk_id = c.id
        WHERE c.workspace_id = ? AND f.chunk_id IS NULL
        """,
        (workspace_id,),
    )
    extra = count_query(
        conn,
        """
        SELECT COUNT(*)
        FROM chunks_fts f
        LEFT JOIN chunks c ON c.id = f.chunk_id
        WHERE f.workspace_id = ? AND c.id IS NULL
        """,
        (workspace_id,),
    )
    workspace_mismatch = count_query(
        conn,
        """
        SELECT COUNT(*)
        FROM chunks c
        JOIN chunks_fts f ON f.chunk_id = c.id
        WHERE c.workspace_id = ? AND f.workspace_id != c.workspace_id
        """,
        (workspace_id,),
    )
    status = (
        "ok"
        if chunk_count == fts_count and missing == 0 and extra == 0 and workspace_mismatch == 0
        else "degraded"
    )
    return IntegrityCheck(
        status=status,
        details={
            "chunks": chunk_count,
            "chunks_fts": fts_count,
            "missing": missing,
            "extra": extra,
            "workspace_mismatch": workspace_mismatch,
        },
    )


def roundtrip_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    sample = sample_query(conn, workspace_id)
    if sample is None:
        return IntegrityCheck(status="unknown", details={"reason": "no searchable chunk"})
    expected_chunk_id, tokens = sample
    tried: list[dict[str, Any]] = []
    for token in tokens:
        hits = search_chunks_fts(conn, workspace_id=workspace_id, query=token, limit=10)
        fts_hit_ids = {hit.chunk_id for hit in hits}
        built = build_context(
            conn,
            RetrievalQuery(workspace_id=workspace_id, query=token, max_tokens=1200),
            embedding_provider=None,
            vector_store=None,
        )
        context_hit_ids = {hit.id for hit in built.hits}
        shared = context_hit_ids & fts_hit_ids
        tried.append(
            {
                "query": token,
                "fts_hits": len(hits),
                "context_hits": len(built.hits),
                "shared_context_fts_hits": len(shared),
            }
        )
        if hits and shared:
            return IntegrityCheck(
                status="ok",
                details={
                    "query": token,
                    "expected_chunk_id": expected_chunk_id,
                    "fts_ok": True,
                    "context_ok": True,
                    "context_hits": len(built.hits),
                    "shared_context_fts_hits": len(shared),
                },
            )
    return IntegrityCheck(
        status="degraded",
        details={
            "query": tokens[0],
            "expected_chunk_id": expected_chunk_id,
            "fts_ok": any(item["fts_hits"] for item in tried),
            "context_ok": False,
            "context_hits": max((item["context_hits"] for item in tried), default=0),
            "shared_context_fts_hits": 0,
            "tried": tried,
        },
    )
