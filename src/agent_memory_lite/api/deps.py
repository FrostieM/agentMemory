"""FastAPI dependency providers.

The embedding provider and vector store are expensive to construct (ST model
load, LanceDB connect) so we cache one of each per process. Tests inject
overrides via `app.dependency_overrides`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.factory import get_embedding_provider
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.factory import get_vector_store

_embedding_singleton: EmbeddingProvider | None = None
_vector_store_singleton: VectorStore | None = None


def get_settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_db_dep(settings: SettingsDep) -> Iterator[sqlite3.Connection]:
    conn = open_connection(settings.db_path)
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


def get_vector_store_dep(settings: SettingsDep) -> VectorStore:
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
