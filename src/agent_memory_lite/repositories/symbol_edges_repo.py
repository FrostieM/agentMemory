"""SQL operations for the ``symbol_edges`` table (1.5.0).

Hard-graph edges between symbol chunks: CALLS / IMPORTS / EXPORTS /
EXTENDS / IMPLEMENTS / REFERENCES / INSTANTIATES / DECORATED_BY.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from agent_memory_lite.models.symbol_edges import (
    ALLOWED_EDGE_TYPES,
    EdgeIn,
    SymbolEdge,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def _row_to_edge(row: sqlite3.Row) -> SymbolEdge:
    return SymbolEdge(
        id=row["id"],
        workspace_id=row["workspace_id"],
        src_chunk_id=row["src_chunk_id"],
        src_qualified_name=row["src_qualified_name"],
        dst_qualified_name=row["dst_qualified_name"],
        dst_chunk_id=row["dst_chunk_id"],
        edge_type=row["edge_type"],
        src_language=row["src_language"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def insert_edge(conn: sqlite3.Connection, edge_in: EdgeIn) -> SymbolEdge:
    if edge_in.edge_type not in ALLOWED_EDGE_TYPES:
        raise ValueError(
            f"Unknown edge_type {edge_in.edge_type!r}. Allowed: {sorted(ALLOWED_EDGE_TYPES)}"
        )
    edge_id = new_id(IdKind.SYMBOL_EDGE)
    created_at = iso_now()
    conn.execute(
        """
        INSERT INTO symbol_edges (
            id, workspace_id, src_chunk_id, src_qualified_name,
            dst_qualified_name, dst_chunk_id, edge_type,
            src_language, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edge_id,
            edge_in.workspace_id,
            edge_in.src_chunk_id,
            edge_in.src_qualified_name,
            edge_in.dst_qualified_name,
            edge_in.dst_chunk_id,
            edge_in.edge_type,
            edge_in.src_language,
            created_at,
            json.dumps(edge_in.metadata, sort_keys=True),
        ),
    )
    return SymbolEdge(
        id=edge_id,
        workspace_id=edge_in.workspace_id,
        src_chunk_id=edge_in.src_chunk_id,
        src_qualified_name=edge_in.src_qualified_name,
        dst_qualified_name=edge_in.dst_qualified_name,
        dst_chunk_id=edge_in.dst_chunk_id,
        edge_type=edge_in.edge_type,
        src_language=edge_in.src_language,
        created_at=created_at,
        metadata=edge_in.metadata,
    )


def insert_edges(conn: sqlite3.Connection, edges: Iterable[EdgeIn]) -> int:
    """Bulk insert. Returns count of edges written."""
    count = 0
    for edge in edges:
        insert_edge(conn, edge)
        count += 1
    return count


def delete_edges_by_src_chunks(
    conn: sqlite3.Connection, *, workspace_id: str, chunk_ids: list[str]
) -> int:
    """Drop all edges whose src_chunk_id is in ``chunk_ids``. Used by
    the file ingest pipeline when a file is re-ingested and the old
    chunks are about to be dropped.
    """
    if not chunk_ids:
        return 0
    placeholders = ", ".join("?" * len(chunk_ids))
    cur = conn.execute(
        f"DELETE FROM symbol_edges WHERE workspace_id = ? AND src_chunk_id IN ({placeholders})",
        [workspace_id, *chunk_ids],
    )
    return int(cur.rowcount)


def list_edges_from(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    src_chunk_id: str,
    edge_types: list[str] | None = None,
    limit: int = 100,
) -> list[SymbolEdge]:
    """Outbound edges from a chunk (downstream — what does this symbol use?)."""
    sql = "SELECT * FROM symbol_edges WHERE workspace_id = ? AND src_chunk_id = ? "
    params: list[object] = [workspace_id, src_chunk_id]
    if edge_types:
        placeholders = ", ".join("?" * len(edge_types))
        sql += f"AND edge_type IN ({placeholders}) "
        params.extend(edge_types)
    sql += "ORDER BY edge_type, dst_qualified_name LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_edge(r) for r in rows]


def list_edges_to(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    dst_qualified_name: str,
    edge_types: list[str] | None = None,
    limit: int = 100,
) -> list[SymbolEdge]:
    """Inbound edges to a symbol (upstream — who depends on this symbol?)."""
    sql = "SELECT * FROM symbol_edges WHERE workspace_id = ? AND dst_qualified_name = ? "
    params: list[object] = [workspace_id, dst_qualified_name]
    if edge_types:
        placeholders = ", ".join("?" * len(edge_types))
        sql += f"AND edge_type IN ({placeholders}) "
        params.extend(edge_types)
    sql += "ORDER BY edge_type, src_qualified_name LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_edge(r) for r in rows]
