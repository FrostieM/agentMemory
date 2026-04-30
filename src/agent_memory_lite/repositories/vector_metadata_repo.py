"""Repository helpers for vector index metadata."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorStore

CHUNKING_STRATEGY = "chunking-v1"
VECTOR_SCHEMA_VERSION = "chunks-v1"


@dataclass(frozen=True, slots=True)
class VectorIndexMetadata:
    workspace_id: str
    namespace: str
    provider_name: str
    embedding_dim: int
    vector_backend: str
    chunking_strategy: str
    schema_version: str
    row_count: int
    updated_at: str


def table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'vector_index_metadata' AND type = 'table'"
    ).fetchone()
    return row is not None


def get_vector_index_metadata(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    namespace: str,
) -> VectorIndexMetadata | None:
    if not table_exists(conn):
        return None
    row = conn.execute(
        """
        SELECT *
        FROM vector_index_metadata
        WHERE workspace_id = ? AND namespace = ?
        """,
        (workspace_id, namespace),
    ).fetchone()
    if row is None:
        return None
    return VectorIndexMetadata(
        workspace_id=str(row["workspace_id"]),
        namespace=str(row["namespace"]),
        provider_name=str(row["provider_name"]),
        embedding_dim=int(row["embedding_dim"]),
        vector_backend=str(row["vector_backend"]),
        chunking_strategy=str(row["chunking_strategy"]),
        schema_version=str(row["schema_version"]),
        row_count=int(row["row_count"]),
        updated_at=str(row["updated_at"]),
    )


def upsert_vector_index_metadata(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    namespace: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    row_count: int,
    chunking_strategy: str = CHUNKING_STRATEGY,
    schema_version: str = VECTOR_SCHEMA_VERSION,
) -> VectorIndexMetadata:
    if not table_exists(conn):
        return VectorIndexMetadata(
            workspace_id=workspace_id,
            namespace=namespace,
            provider_name=provider.name,
            embedding_dim=provider.dim,
            vector_backend=store.backend,
            chunking_strategy=chunking_strategy,
            schema_version=schema_version,
            row_count=row_count,
            updated_at=iso_now(),
        )
    timestamp = iso_now()
    conn.execute(
        """
        INSERT INTO vector_index_metadata (
            workspace_id, namespace, provider_name, embedding_dim,
            vector_backend, chunking_strategy, schema_version, row_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, namespace) DO UPDATE SET
            provider_name = excluded.provider_name,
            embedding_dim = excluded.embedding_dim,
            vector_backend = excluded.vector_backend,
            chunking_strategy = excluded.chunking_strategy,
            schema_version = excluded.schema_version,
            row_count = excluded.row_count,
            updated_at = excluded.updated_at
        """,
        (
            workspace_id,
            namespace,
            provider.name,
            provider.dim,
            store.backend,
            chunking_strategy,
            schema_version,
            row_count,
            timestamp,
        ),
    )
    return VectorIndexMetadata(
        workspace_id=workspace_id,
        namespace=namespace,
        provider_name=provider.name,
        embedding_dim=provider.dim,
        vector_backend=store.backend,
        chunking_strategy=chunking_strategy,
        schema_version=schema_version,
        row_count=row_count,
        updated_at=timestamp,
    )


def provider_name_from_settings(*, embedding_backend: str, embedding_model: str) -> str:
    if embedding_backend == "sentence_transformers":
        return f"st:{embedding_model}"
    return f"{embedding_backend}:{embedding_model}"
