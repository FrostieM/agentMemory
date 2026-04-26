from __future__ import annotations

from agent_memory_lite.vector_store.namespaces import (
    ALL_NAMESPACES,
    NAMESPACE_CHUNKS,
    NAMESPACE_ENTITIES,
)


def test_namespace_constants_are_strings() -> None:
    assert isinstance(NAMESPACE_CHUNKS, str)
    assert isinstance(NAMESPACE_ENTITIES, str)


def test_all_namespaces_lists_both() -> None:
    assert NAMESPACE_CHUNKS in ALL_NAMESPACES
    assert NAMESPACE_ENTITIES in ALL_NAMESPACES
    assert len(set(ALL_NAMESPACES)) == len(ALL_NAMESPACES)


def test_namespaces_are_distinct() -> None:
    assert NAMESPACE_CHUNKS != NAMESPACE_ENTITIES
