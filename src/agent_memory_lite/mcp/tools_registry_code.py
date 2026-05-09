"""1.4.0+: code-memory MCP tool definitions.

Holds the ``ToolDefinition`` rows for ``memory_find_symbols``
(v1.4.0), ``memory_graph_neighbors`` (v1.5.0),
``memory_symbol_history`` (v1.6.0), and ``memory_breaking_changes``
(v1.6.0). Split from ``tools_registry_core.py`` so the core registry
stays under the SLOC ceiling as the code-memory surface grows.
"""

from __future__ import annotations

from agent_memory_lite.mcp.tools_code_graph import memory_code_graph
from agent_memory_lite.mcp.tools_coordination import (
    memory_claim_edit,
    memory_list_active_edits,
    memory_release_edit,
    memory_soft_neighbors,
)
from agent_memory_lite.mcp.tools_digests import (
    memory_file_digest,
    memory_list_file_digests,
)
from agent_memory_lite.mcp.tools_graph import memory_graph_neighbors
from agent_memory_lite.mcp.tools_overview import memory_code_overview
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
    ToolDefinition(
        name="memory_claim_edit",
        description=(
            "1.7.0: claim a symbol or file for editing. Multi-agent "
            "coordination — other agents see this lock via "
            "memory_list_active_edits."
        ),
        handler=memory_claim_edit,
    ),
    ToolDefinition(
        name="memory_release_edit",
        description="1.7.0: release a previously-claimed edit lock by claim_id.",
        handler=memory_release_edit,
    ),
    ToolDefinition(
        name="memory_list_active_edits",
        description=("1.7.0: list every non-expired edit claim in the workspace."),
        handler=memory_list_active_edits,
    ),
    ToolDefinition(
        name="memory_soft_neighbors",
        description=(
            "1.7.0: heuristic graph neighbors — symbols that co-change "
            "or co-reference. Use after the hard graph misses an edge "
            "you expected."
        ),
        handler=memory_soft_neighbors,
    ),
    ToolDefinition(
        name="memory_file_digest",
        description=(
            "1.8.0: narrative + structured digest for one file "
            "(chunk count, symbol kinds, edge counts, recent versions)."
        ),
        handler=memory_file_digest,
    ),
    ToolDefinition(
        name="memory_list_file_digests",
        description=("1.8.0: workspace overview — every file's digest, newest first."),
        handler=memory_list_file_digests,
    ),
    ToolDefinition(
        name="memory_code_overview",
        description=(
            "2.0: full workspace dashboard payload (counts, recent "
            "files, breaking changes, active edits, hot symbols)."
        ),
        handler=memory_code_overview,
    ),
    ToolDefinition(
        name="memory_code_graph",
        description=(
            "2.1.2: node-link subgraph (BFS from a center symbol or "
            "top-K connected overview) for the D3 dashboard."
        ),
        handler=memory_code_graph,
    ),
)
