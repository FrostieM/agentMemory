"""Vector-index metadata validation for the vector-store integrity check."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.repositories.vector_metadata_repo import (
    CHUNKING_STRATEGY,
    VECTOR_SCHEMA_VERSION,
    get_vector_index_metadata,
)
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS


def _check_metadata(
    conn: sqlite3.Connection,
    workspace_id: str,
    vector_ids: set[str],
    *,
    expected_provider_name: str | None,
    expected_vector_backend: str | None,
) -> tuple[str, dict[str, Any]]:
    metadata = get_vector_index_metadata(
        conn, workspace_id=workspace_id, namespace=NAMESPACE_CHUNKS
    )
    metadata_status = "ok"
    metadata_details: dict[str, Any] = {}
    if metadata is None:
        metadata_status = "warning" if vector_ids else "unknown"
        metadata_details["reason"] = "vector_index_metadata_missing"
        return metadata_status, metadata_details
    metadata_details = {
        "provider_name": metadata.provider_name,
        "embedding_dim": metadata.embedding_dim,
        "vector_backend": metadata.vector_backend,
        "chunking_strategy": metadata.chunking_strategy,
        "schema_version": metadata.schema_version,
        "row_count": metadata.row_count,
        "updated_at": metadata.updated_at,
    }
    metadata_mismatches: dict[str, dict[str, Any]] = {}
    if metadata.row_count != len(vector_ids):
        metadata_mismatches["row_count"] = {
            "expected": len(vector_ids),
            "actual": metadata.row_count,
        }
    if expected_provider_name and metadata.provider_name != expected_provider_name:
        metadata_mismatches["provider_name"] = {
            "expected": expected_provider_name,
            "actual": metadata.provider_name,
        }
    if expected_vector_backend and metadata.vector_backend != expected_vector_backend:
        metadata_mismatches["vector_backend"] = {
            "expected": expected_vector_backend,
            "actual": metadata.vector_backend,
        }
    if metadata.chunking_strategy != CHUNKING_STRATEGY:
        metadata_mismatches["chunking_strategy"] = {
            "expected": CHUNKING_STRATEGY,
            "actual": metadata.chunking_strategy,
        }
    if metadata.schema_version != VECTOR_SCHEMA_VERSION:
        metadata_mismatches["schema_version"] = {
            "expected": VECTOR_SCHEMA_VERSION,
            "actual": metadata.schema_version,
        }
    if metadata_mismatches:
        metadata_status = "degraded"
        metadata_details["mismatches"] = metadata_mismatches
    return metadata_status, metadata_details
