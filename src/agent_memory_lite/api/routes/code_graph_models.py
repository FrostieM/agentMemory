"""2.1.2: response models for /memory/code_graph.

Split from ``code_graph.py`` so the route stays under SLOC. Three
small pydantic types describing a node-link subgraph the D3.js
dashboard renders.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualified_name: str
    language: str | None
    symbol_kind: str | None
    file_path: str | None
    degree: int  # inbound + outbound for ordering / sizing


class GraphLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str  # source qualified_name (D3 expects 'source'/'target')
    target: str
    edge_type: str


class CodeGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    center: str | None
    depth: int
    nodes: list[GraphNode]
    links: list[GraphLink]
    truncated: bool  # True when max_nodes cap was hit
