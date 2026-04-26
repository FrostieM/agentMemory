"""Vector namespace constants.

Two namespaces ship in v1: chunks (text-bearing rows) and entities (graph nodes).
Hard-coded constants prevent typos and keep the surface stable.
"""

from __future__ import annotations

NAMESPACE_CHUNKS = "chunks"
NAMESPACE_ENTITIES = "entities"

ALL_NAMESPACES: tuple[str, ...] = (NAMESPACE_CHUNKS, NAMESPACE_ENTITIES)
