"""1.4.0+: code-memory MCP tool definitions.

Holds the ``ToolDefinition`` rows for ``memory_find_symbols``
(v1.4.0), ``memory_graph_neighbors`` (v1.5.0),
``memory_symbol_history`` (v1.6.0), and ``memory_breaking_changes``
(v1.6.0). Split from ``tools_registry_core.py`` so the core registry
stays under the SLOC ceiling as the code-memory surface grows.
"""

from __future__ import annotations

from agent_memory_lite.mcp.tools_graph import memory_graph_neighbors
from agent_memory_lite.mcp.tools_payloads import ToolDefinition
from agent_memory_lite.mcp.tools_symbols import memory_find_symbols
from agent_memory_lite.mcp.tools_versions import (
    memory_breaking_changes,
    memory_symbol_history,
)

CODE_TOOLS_REGISTRY: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="memory_find_symbols",
        description=(
            "1.4.0: exact symbol-level chunk lookup by qualified_name "
            "(e.g. 'Class.method' or 'Class::method' for C++) across "
            "the top-7 supported languages."
        ),
        handler=memory_find_symbols,
    ),
    ToolDefinition(
        name="memory_graph_neighbors",
        description=(
            "1.5.0: hard-graph upstream / downstream lookup. Given a "
            "symbol's qualified_name (upstream — who depends on it?) "
            "or chunk_id (downstream — what does it depend on?), "
            "return CALLS / IMPORTS / EXTENDS / IMPLEMENTS / "
            "REFERENCES / INSTANTIATES / DECORATED_BY edges."
        ),
        handler=memory_graph_neighbors,
    ),
    ToolDefinition(
        name="memory_symbol_history",
        description=(
            "1.6.0: version history for one symbol — every signature + "
            "content snapshot recorded over time."
        ),
        handler=memory_symbol_history,
    ),
    ToolDefinition(
        name="memory_breaking_changes",
        description=(
            "1.6.0: list recent signature-changing edits with downstream "
            "caller counts. 'Who could break after my last refactor?'"
        ),
        handler=memory_breaking_changes,
    ),
)
