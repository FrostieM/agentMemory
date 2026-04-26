"""Lite temporal graph: entities, facts, conflict detection, traversal."""

from agent_memory_lite.graph.canonicalize import canonicalize_name
from agent_memory_lite.graph.conflict_detector import find_conflicting_facts
from agent_memory_lite.graph.invalidate import invalidate_facts
from agent_memory_lite.graph.traversal import GraphHit, traverse_facts
from agent_memory_lite.graph.upsert_entity import upsert_entity
from agent_memory_lite.graph.write_fact import write_fact

__all__ = [
    "GraphHit",
    "canonicalize_name",
    "find_conflicting_facts",
    "invalidate_facts",
    "traverse_facts",
    "upsert_entity",
    "write_fact",
]
