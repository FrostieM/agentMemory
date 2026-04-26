"""FastAPI dependency providers.

The embedding provider is cached one-per-process (model load is slow).
The DB connection is opened per-request and respects an optional
`X-Memory-DB-Path` request header so a single HTTP service can serve
multiple per-project memory files (used by the project-scoped
UserPromptSubmit hook). The vector store is cached unless the request
overrides `X-Memory-Vector-Path`, in which case a request-scoped store
is opened.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.factory import get_vector_store
from agent_memory_lite.vector_store.lancedb_store import LanceDBStore
from agent_memory_lite.vector_store.sqlite_vec_store import SqliteVecStore

_embedding_singleton: EmbeddingProvider | None = None
_vector_store_singleton: VectorStore | None = None


def get_settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def _resolve_db_path(request: Request, settings: Settings) -> Path:
    override = request.headers.get("x-memory-db-path")
    if override:
        return Path(override)
    return settings.db_path


def get_db_dep(request: Request, settings: SettingsDep) -> Iterator[sqlite3.Connection]:
    conn = open_connection(_resolve_db_path(request, settings))
    try:
        yield conn
    finally:
        close_connection(conn)


DbDep = Annotated[sqlite3.Connection, Depends(get_db_dep)]


def get_embedding_provider_dep(settings: SettingsDep) -> EmbeddingProvider:
    global _embedding_singleton  # noqa: PLW0603
    if _embedding_singleton is None:
        _embedding_singleton = get_embedding_provider(settings)
    return _embedding_singleton


EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider_dep)]


def _build_request_scoped_store(settings: Settings, override: str) -> VectorStore:
    if settings.vector_backend == "sqlite_vec":
        return SqliteVecStore(override)
    return LanceDBStore(override)


def get_vector_store_dep(request: Request, settings: SettingsDep) -> VectorStore:
    override = request.headers.get("x-memory-vector-path")
    if override:
        return _build_request_scoped_store(settings, override)
    global _vector_store_singleton  # noqa: PLW0603
    if _vector_store_singleton is None:
        _vector_store_singleton = get_vector_store(settings)
    return _vector_store_singleton


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store_dep)]


def reset_dependency_singletons() -> None:
    """Test hook: drop cached provider + vector store so a new factory call runs."""
    global _embedding_singleton, _vector_store_singleton  # noqa: PLW0603
    _embedding_singleton = None
    _vector_store_singleton = None
