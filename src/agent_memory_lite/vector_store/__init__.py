"""Embedded vector store. LanceDB by default; sqlite-vec opt-in."""

from agent_memory_lite.vector_store.base import (
    VectorHit,
    VectorRow,
    VectorStore,
    VectorStoreUnavailableError,
)
from agent_memory_lite.vector_store.factory import get_vector_store
from agent_memory_lite.vector_store.namespaces import (
    NAMESPACE_CHUNKS,
    NAMESPACE_ENTITIES,
)

__all__ = [
    "NAMESPACE_CHUNKS",
    "NAMESPACE_ENTITIES",
    "VectorHit",
    "VectorRow",
    "VectorStore",
    "VectorStoreUnavailableError",
    "get_vector_store",
]
