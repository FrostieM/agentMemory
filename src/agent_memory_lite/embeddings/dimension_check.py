"""Workspace embedding-dimension drift check.

On first ingest the provider's `(name, dim)` is pinned in `workspace_meta`. On
subsequent boots/ingests we compare. If the provider produces a different
dim, the service refuses to mix old + new vectors and points at
`scripts/reindex_vectors.py`.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config import workspace_meta
from agent_memory_lite.embeddings.base import EmbeddingProvider

KEY_MODEL = "embedding_model"
KEY_DIM = "embedding_dim"


class EmbeddingDimensionMismatchError(RuntimeError):
    """Raised when the configured embedding model differs from the pinned one."""


def pin_or_check(
    conn: sqlite3.Connection,
    workspace_id: str,
    provider: EmbeddingProvider,
) -> None:
    pinned_model = workspace_meta.get(conn, workspace_id, KEY_MODEL)
    pinned_dim = workspace_meta.get(conn, workspace_id, KEY_DIM)
    if pinned_model is None or pinned_dim is None:
        workspace_meta.set_value(conn, workspace_id, KEY_MODEL, provider.name)
        workspace_meta.set_value(conn, workspace_id, KEY_DIM, str(provider.dim))
        return
    if pinned_model != provider.name or int(pinned_dim) != provider.dim:
        raise EmbeddingDimensionMismatchError(
            f"workspace {workspace_id!r} pinned to model={pinned_model} dim={pinned_dim};"
            f" provider reports model={provider.name} dim={provider.dim}."
            " Run scripts/reindex_vectors.py to rebuild."
        )
