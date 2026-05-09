"""1.5.0: hard-graph edges between symbol chunks.

A ``SymbolEdge`` connects a source chunk (where the edge is written —
the function body that contains the call site, the class declaration
that extends a base) to a destination symbol identified by its
qualified name. The destination's chunk_id is denormalized for fast
upstream / downstream lookups but is nullable: edges to symbols
outside the workspace (stdlib, third-party imports, methods we
haven't seen yet) are still recorded so a later resolver pass can
populate the chunk_id when the target lands.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EdgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    src_chunk_id: str
    src_qualified_name: str = Field(max_length=400)
    dst_qualified_name: str = Field(max_length=400)
    dst_chunk_id: str | None = None
    edge_type: str = Field(max_length=32)
    src_language: str | None = Field(default=None, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SymbolEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    src_chunk_id: str
    src_qualified_name: str
    dst_qualified_name: str
    dst_chunk_id: str | None
    edge_type: str
    src_language: str | None
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# Allowed edge_type values. Validation happens at the extractor +
# repo boundary so a typo in extraction code surfaces immediately
# instead of silently writing junk into the graph.
ALLOWED_EDGE_TYPES: frozenset[str] = frozenset(
    {
        "calls",
        "imports",
        "exports",
        "extends",
        "implements",
        "references",
        "instantiates",
        "decorated_by",
    }
)
