"""Vector-store integrity check."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.maintenance.integrity_models import IntegrityCheck
from agent_memory_lite.maintenance.integrity_vector_metadata import _check_metadata
from agent_memory_lite.repositories.chunks_repo import (
    EMBEDDING_STALE_REASON_KEY,
    EMBEDDING_TEXT_SHA256_KEY,
    chunk_text_sha256,
)
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

__all__ = ["vector_check"]


def _embedding_hash_details(rows: list[sqlite3.Row]) -> dict[str, Any]:
    details: dict[str, Any] = {
        "missing_embedding_ids": 0,
        "missing_embedding_hashes": 0,
        "stale_embedding_hashes": 0,
        "missing_embedding_hash_ids_sample": [],
        "stale_embedding_hash_ids_sample": [],
    }
    for row in rows:
        chunk_id = str(row["id"])
        if row["embedding_id"] is None or row["embedding_id"] != chunk_id:
            details["missing_embedding_ids"] += 1
            continue
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        stored_hash = metadata.get(EMBEDDING_TEXT_SHA256_KEY)
        stale_reason = metadata.get(EMBEDDING_STALE_REASON_KEY)
        current_hash = chunk_text_sha256(str(row["text"]))
        if stale_reason:
            details["stale_embedding_hashes"] += 1
            if len(details["stale_embedding_hash_ids_sample"]) < 10:
                details["stale_embedding_hash_ids_sample"].append(chunk_id)
        elif stored_hash is None:
            details["missing_embedding_hashes"] += 1
            if len(details["missing_embedding_hash_ids_sample"]) < 10:
                details["missing_embedding_hash_ids_sample"].append(chunk_id)
        elif stored_hash != current_hash:
            details["stale_embedding_hashes"] += 1
            if len(details["stale_embedding_hash_ids_sample"]) < 10:
                details["stale_embedding_hash_ids_sample"].append(chunk_id)
    return details


def vector_check(
    conn: sqlite3.Connection,
    workspace_id: str,
    vector_store: VectorStore | None,
    *,
    expected_provider_name: str | None = None,
    expected_vector_backend: str | None = None,
) -> IntegrityCheck:
    chunk_ids = {
        str(row[0])
        for row in conn.execute(
            "SELECT id FROM chunks WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    }
    if vector_store is None:
        return IntegrityCheck(
            status="unknown",
            details={"chunks": len(chunk_ids), "reason": "vector_store_not_supplied"},
        )
    try:
        vector_store.open()
        # round-D: CLOSE the store on every path. It was opened but never closed on
        # either the success or the error path, leaking the LanceDB/sqlite connection +
        # file handles each integrity pass. vector_ids is a local set, so the store is
        # done being used the moment list_ids returns.
        try:
            vector_ids = set(vector_store.list_ids(NAMESPACE_CHUNKS, workspace_id=workspace_id))
        finally:
            vector_store.close()
    except Exception as exc:
        return IntegrityCheck(
            status="unknown",
            details={"chunks": len(chunk_ids), "error": str(exc)},
        )
    missing = sorted(chunk_ids - vector_ids)
    extra = sorted(vector_ids - chunk_ids)
    embedding_details: dict[str, Any] = {
        "missing_embedding_ids": 0,
        "missing_embedding_hashes": 0,
        "stale_embedding_hashes": 0,
        "missing_embedding_hash_ids_sample": [],
        "stale_embedding_hash_ids_sample": [],
    }
    if vector_ids:
        placeholders = ",".join("?" for _ in vector_ids)
        rows = conn.execute(
            f"""
            SELECT id, text, embedding_id, metadata_json
            FROM chunks
            WHERE workspace_id = ?
              AND id IN ({placeholders})
            """,
            (workspace_id, *sorted(vector_ids)),
        ).fetchall()
        embedding_details = _embedding_hash_details(rows)

    metadata_status, metadata_details = _check_metadata(
        conn,
        workspace_id,
        vector_ids,
        expected_provider_name=expected_provider_name,
        expected_vector_backend=expected_vector_backend,
    )

    if (
        missing
        or extra
        or embedding_details["stale_embedding_hashes"]
        or metadata_status == "degraded"
    ):
        status = "degraded"
    elif (
        embedding_details["missing_embedding_ids"]
        or embedding_details["missing_embedding_hashes"]
        or metadata_status == "warning"
    ):
        status = "warning"
    else:
        status = "ok"
    return IntegrityCheck(
        status=status,
        details={
            "chunks": len(chunk_ids),
            "vectors": len(vector_ids),
            "missing": len(missing),
            "extra": len(extra),
            "missing_embedding_ids": embedding_details["missing_embedding_ids"],
            "missing_embedding_hashes": embedding_details["missing_embedding_hashes"],
            "stale_embedding_hashes": embedding_details["stale_embedding_hashes"],
            "missing_ids_sample": missing[:10],
            "extra_ids_sample": extra[:10],
            "missing_embedding_hash_ids_sample": embedding_details[
                "missing_embedding_hash_ids_sample"
            ],
            "stale_embedding_hash_ids_sample": embedding_details["stale_embedding_hash_ids_sample"],
            "metadata_status": metadata_status,
            "metadata": metadata_details,
        },
    )
