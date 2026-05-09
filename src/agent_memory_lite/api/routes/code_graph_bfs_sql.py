"""2.1.2: SQL helpers for the code-graph BFS builder.

Split from ``code_graph_bfs.py`` to keep both modules under the
SLOC ceiling. Two reads:

* ``node_metadata`` — fetch (language, symbol_kind, file_path) for
  a set of qualified_names from ``chunks``.
* ``edges_touching`` — fetch every symbol_edges row whose src or
  dst is in ``qnames``, optionally filtered by edge_type.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.api.routes.code_graph_models import GraphNode


def node_metadata(
    conn: sqlite3.Connection, *, workspace_id: str, qnames: set[str]
) -> dict[str, GraphNode]:
    if not qnames:
        return {}
    placeholders = ", ".join("?" * len(qnames))
    rows = conn.execute(
        f"SELECT qualified_name, MIN(symbol_kind) AS sk, "
        f"MIN(metadata_json) AS meta "
        f"FROM chunks WHERE workspace_id = ? "
        f"AND qualified_name IN ({placeholders}) "
        f"GROUP BY qualified_name",
        [workspace_id, *qnames],
    ).fetchall()
    out: dict[str, GraphNode] = {}
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        out[str(r["qualified_name"])] = GraphNode(
            qualified_name=str(r["qualified_name"]),
            language=meta.get("language"),
            symbol_kind=r["sk"],
            file_path=meta.get("path"),
            degree=0,
        )
    return out


def edges_touching(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    qnames: set[str],
    edge_kinds: list[str] | None,
) -> list[tuple[str, str, str]]:
    if not qnames:
        return []
    placeholders = ", ".join("?" * len(qnames))
    sql = (
        f"SELECT src_qualified_name, dst_qualified_name, edge_type "
        f"FROM symbol_edges WHERE workspace_id = ? "
        f"AND (src_qualified_name IN ({placeholders}) "
        f"OR dst_qualified_name IN ({placeholders}))"
    )
    params: list[object] = [workspace_id, *qnames, *qnames]
    if edge_kinds:
        sql += " AND edge_type IN (" + ", ".join("?" * len(edge_kinds)) + ")"
        params.extend(edge_kinds)
    rows = conn.execute(sql, params).fetchall()
    return [
        (str(r["src_qualified_name"]), str(r["dst_qualified_name"]), str(r["edge_type"]))
        for r in rows
    ]
