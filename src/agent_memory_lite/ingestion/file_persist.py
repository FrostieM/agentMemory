"""Vector-side persistence helper for the file ingest pipeline.

Split out of ``file_pipeline.py`` so the orchestrator stays under the
SLOC ceiling. Vector upsert runs after the SQLite transaction commits;
on failure the orchestrator records a maintenance event.
"""

from __future__ import annotations

import sqlite3

import numpy as np

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.enums import MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEventIn
from agent_memory_lite.repositories.chunks_repo import (
    EMBEDDING_TEXT_SHA256_KEY,
    chunk_text_sha256,
    set_chunk_embedding_id,
)
from agent_memory_lite.vector_store.base import VectorRow, VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

_log = get_logger("ingestion.file_persist")


def embed_and_upsert(
    chunk_id: str,
    workspace_id: str,
    text: str,
    file_path: str,
    provider: EmbeddingProvider,
    store: VectorStore,
) -> bool:
    try:
        vectors = provider.embed_batch([text], kind="doc")
        store.upsert(
            NAMESPACE_CHUNKS,
            [
                VectorRow(
                    id=chunk_id,
                    workspace_id=workspace_id,
                    vector=np.asarray(vectors[0]),
                    metadata={
                        "chunk_id": chunk_id,
                        "kind": "code",
                        "path": file_path,
                        EMBEDDING_TEXT_SHA256_KEY: chunk_text_sha256(text),
                    },
                )
            ],
        )
    except Exception as exc:
        _log.warning("file_vector_upsert_failed", chunk_id=chunk_id, error=str(exc))
        return False
    return True


def run_vector_phase(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    path: str,
    new_chunks: list[tuple[str, str, list[str]]],
    provider: EmbeddingProvider,
    store: VectorStore,
) -> None:
    """Embed each chunk; write a maintenance event when upsert fails."""
    for chunk_id, text, _symbols in new_chunks:
        embedded = embed_and_upsert(chunk_id, workspace_id, text, path, provider, store)
        if embedded:
            set_chunk_embedding_id(
                conn,
                chunk_id=chunk_id,
                embedding_id=chunk_id,
                embedding_text_hash=chunk_text_sha256(text),
            )
        else:
            write_maintenance_event(
                conn,
                MaintenanceEventIn(
                    workspace_id=workspace_id,
                    kind="vector_upsert_failed",
                    severity=MaintenanceSeverity.ERROR,
                    summary="Vector upsert failed after the file SQLite write committed.",
                    details={"chunk_id": chunk_id, "path": path},
                    target_type="chunk",
                    target_id=chunk_id,
                ),
            )
