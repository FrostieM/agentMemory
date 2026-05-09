"""2.1.2: BFS subgraph builder for /memory/code_graph.

Two modes:
* center mode: BFS up to ``depth`` hops outward from a single
  qualified_name; both directions, all edge_kinds (or filtered).
* overview mode: top-K most-connected qualified_names by total
  edge degree (inbound + outbound), with their inter-edges.

SQL helpers live in ``code_graph_bfs_sql.py``; this module owns
the BFS / overview orchestration + materialization.
"""

from __future__ import annotations

import sqlite3
from collections import deque

from agent_memory_lite.api.routes.code_graph_bfs_sql import (
    edges_touching,
    node_metadata,
)
from agent_memory_lite.api.routes.code_graph_models import GraphLink, GraphNode


def bfs_from_center(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    center: str,
    depth: int,
    max_nodes: int,
    edge_kinds: list[str] | None,
) -> tuple[list[GraphNode], list[GraphLink], bool]:
    visited: set[str] = {center}
    frontier: deque[tuple[str, int]] = deque([(center, 0)])
    edges: list[tuple[str, str, str]] = []
    truncated = False
    while frontier:
        cur, hop = frontier.popleft()
        if hop >= depth:
            continue
        new_edges = edges_touching(
            conn, workspace_id=workspace_id, qnames={cur}, edge_kinds=edge_kinds
        )
        for src, dst, kind in new_edges:
            edges.append((src, dst, kind))
            for neighbor in (src, dst):
                if neighbor in visited:
                    continue
                if len(visited) >= max_nodes:
                    truncated = True
                    continue
                visited.add(neighbor)
                frontier.append((neighbor, hop + 1))
    return _materialize(conn, workspace_id, visited, edges, truncated)


def overview(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    max_nodes: int,
    edge_kinds: list[str] | None,
) -> tuple[list[GraphNode], list[GraphLink], bool]:
    rows = conn.execute(
        "SELECT qn, SUM(c) AS total FROM ("
        "  SELECT src_qualified_name AS qn, COUNT(*) AS c "
        "  FROM symbol_edges WHERE workspace_id = ? GROUP BY src_qualified_name "
        "  UNION ALL "
        "  SELECT dst_qualified_name AS qn, COUNT(*) AS c "
        "  FROM symbol_edges WHERE workspace_id = ? GROUP BY dst_qualified_name "
        ") GROUP BY qn ORDER BY total DESC LIMIT ?",
        (workspace_id, workspace_id, max_nodes),
    ).fetchall()
    visited = {str(r["qn"]) for r in rows}
    edges = edges_touching(conn, workspace_id=workspace_id, qnames=visited, edge_kinds=edge_kinds)
    # filter edges to only those whose BOTH ends are in visited
    edges = [(s, d, k) for (s, d, k) in edges if s in visited and d in visited]
    return _materialize(conn, workspace_id, visited, edges, truncated=False)


def _materialize(
    conn: sqlite3.Connection,
    workspace_id: str,
    qnames: set[str],
    edges: list[tuple[str, str, str]],
    truncated: bool,
) -> tuple[list[GraphNode], list[GraphLink], bool]:
    metadata = node_metadata(conn, workspace_id=workspace_id, qnames=qnames)
    degrees: dict[str, int] = dict.fromkeys(qnames, 0)
    seen_links: set[tuple[str, str, str]] = set()
    out_links: list[GraphLink] = []
    for src, dst, kind in edges:
        key = (src, dst, kind)
        if key in seen_links:
            continue
        seen_links.add(key)
        out_links.append(GraphLink(source=src, target=dst, edge_type=kind))
        if src in degrees:
            degrees[src] += 1
        if dst in degrees:
            degrees[dst] += 1
    out_nodes: list[GraphNode] = []
    for qn in sorted(qnames, key=lambda n: -degrees.get(n, 0)):
        meta = metadata.get(qn)
        out_nodes.append(
            GraphNode(
                qualified_name=qn,
                language=meta.language if meta is not None else None,
                symbol_kind=meta.symbol_kind if meta is not None else None,
                file_path=meta.file_path if meta is not None else None,
                degree=degrees.get(qn, 0),
            )
        )
    return out_nodes, out_links, truncated
