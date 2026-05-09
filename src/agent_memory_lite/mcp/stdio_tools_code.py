"""1.4.0 / 1.5.0: stdio tool schemas for code-memory tools.

Split from ``stdio_tools_episodes.py`` so the original file stays
under the SLOC ceiling. Holds ``memory_find_symbols`` (v1.4.0,
qualified-name lookup) and ``memory_graph_neighbors`` (v1.5.0,
upstream / downstream edge traversal).
"""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

CODE_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_find_symbols",
        description=(
            "1.4.0: exact symbol-level lookup. Match by qualified_name "
            "('Class.method', 'Class::method' for C++) or prefix. "
            "Filters by symbol_kind (function/class/method/struct/"
            "interface/enum/type) and language. Returns the chunk body "
            "directly so a Class.method search lands on the method body."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "name": {"type": "string", "maxLength": 400},
                "name_prefix": {"type": "string", "maxLength": 400},
                "kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "function",
                            "class",
                            "method",
                            "struct",
                            "interface",
                            "enum",
                            "type",
                        ],
                    },
                },
                "languages": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
            },
        },
    ),
    types.Tool(
        name="memory_graph_neighbors",
        description=(
            "1.5.0: hard-graph upstream / downstream lookup. Pass "
            "qualified_name to find inbound edges (who depends on it?), "
            "chunk_id to find outbound edges (what does it depend on?), "
            "or both for a full neighborhood."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "qualified_name": {"type": "string", "maxLength": 400},
                "chunk_id": {"type": "string", "maxLength": 64},
                "edge_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "calls",
                            "imports",
                            "exports",
                            "extends",
                            "implements",
                            "references",
                            "instantiates",
                            "decorated_by",
                        ],
                    },
                },
                "direction": {
                    "type": "string",
                    "enum": ["upstream", "downstream", "both"],
                    "default": "both",
                },
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            },
        },
    ),
]
