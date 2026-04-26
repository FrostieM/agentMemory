"""Retrieval pipeline.

The single public entry point is `build_context`, which orchestrates the FTS,
vector, fusion, scoring, filter, and rendering steps.
"""

from agent_memory_lite.retrieval.context_builder import (
    BuiltContext,
    build_context,
)

__all__ = ["BuiltContext", "build_context"]
