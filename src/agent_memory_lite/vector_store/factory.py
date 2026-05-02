"""Pick a `VectorStore` from settings, with a sqlite-vec → LanceDB fallback."""

from __future__ import annotations

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.vector_store.base import (
    VectorStore,
    VectorStoreUnavailableError,
)
from agent_memory_lite.vector_store.lancedb_store import LanceDBStore
from agent_memory_lite.vector_store.sqlite_vec_store import SqliteVecStore

_log = get_logger("vector_store.factory")


def get_vector_store(settings: Settings) -> VectorStore:
    if settings.vector_backend == "sqlite_vec":
        try:
            store = SqliteVecStore(settings.vector_db_path)
            store.open()
            return store
        except VectorStoreUnavailableError as exc:
            _log.warning(
                "sqlite_vec_unavailable_falling_back_to_lancedb",
                error=str(exc),
            )
    return LanceDBStore(settings.vector_db_path)
