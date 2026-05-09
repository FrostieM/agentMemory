"""2.0: SQL helpers for /memory/code_overview.

Counts + top-called aggregations over the v1.4-v1.8 code-memory
tables. Pure read; no mutations.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.routes.code_overview_models import CodeCounts, HotSymbol


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row is not None else 0


def gather_counts(conn: sqlite3.Connection, workspace_id: str) -> CodeCounts:
    return CodeCounts(
        files=_scalar(
            conn,
            "SELECT COUNT(*) FROM file_digests WHERE workspace_id = ?",
            (workspace_id,),
        ),
        chunks=_scalar(
            conn,
            "SELECT COUNT(*) FROM chunks WHERE workspace_id = ? AND qualified_name IS NOT NULL",
            (workspace_id,),
        ),
        symbols=_scalar(
            conn,
            "SELECT COUNT(DISTINCT qualified_name) FROM chunks "
            "WHERE workspace_id = ? AND qualified_name IS NOT NULL",
            (workspace_id,),
        ),
        edges=_scalar(
            conn,
            "SELECT COUNT(*) FROM symbol_edges WHERE workspace_id = ?",
            (workspace_id,),
        ),
        versions=_scalar(
            conn,
            "SELECT COUNT(*) FROM symbol_versions WHERE workspace_id = ?",
            (workspace_id,),
        ),
        soft_edges=_scalar(
            conn,
            "SELECT COUNT(*) FROM soft_edges WHERE workspace_id = ?",
            (workspace_id,),
        ),
    )


def gather_top_called(conn: sqlite3.Connection, workspace_id: str, limit: int) -> list[HotSymbol]:
    rows = conn.execute(
        "SELECT dst_qualified_name AS qn, COUNT(*) AS n "
        "FROM symbol_edges WHERE workspace_id = ? "
        "AND edge_type IN ('calls', 'instantiates') "
        "GROUP BY dst_qualified_name ORDER BY n DESC, qn ASC LIMIT ?",
        (workspace_id, limit),
    ).fetchall()
    return [HotSymbol(qualified_name=str(r["qn"]), inbound_calls=int(r["n"])) for r in rows]
