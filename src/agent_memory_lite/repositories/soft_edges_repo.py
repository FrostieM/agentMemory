"""SQL operations for the ``soft_edges`` table (1.7.0)."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.soft_edges import ALLOWED_SOFT_KINDS, SoftEdge
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def _row_to_edge(row: sqlite3.Row) -> SoftEdge:
    return SoftEdge(
        id=row["id"],
        workspace_id=row["workspace_id"],
        src_qualified_name=row["src_qualified_name"],
        dst_qualified_name=row["dst_qualified_name"],
        edge_kind=row["edge_kind"],
        weight=float(row["weight"]),
        observation_count=int(row["observation_count"]),
        last_seen_at=row["last_seen_at"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def upsert_soft_edge(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    src: str,
    dst: str,
    kind: str,
    weight_increment: float = 1.0,
) -> None:
    """Increment weight + observation_count for an existing soft edge,
    or insert a new row if it doesn't exist. Order-stable: the pair
    (src, dst) is treated as directional ``src → dst``; we do NOT
    auto-mirror to ``dst → src`` here — the caller picks ordering.
    """
    if kind not in ALLOWED_SOFT_KINDS:
        raise ValueError(f"Unknown soft edge kind {kind!r}. Allowed: {sorted(ALLOWED_SOFT_KINDS)}")
    now = iso_now()
    cur = conn.execute(
        "UPDATE soft_edges "
        "SET weight = weight + ?, observation_count = observation_count + 1, "
        "last_seen_at = ? "
        "WHERE workspace_id = ? AND src_qualified_name = ? "
        "AND dst_qualified_name = ? AND edge_kind = ?",
        (weight_increment, now, workspace_id, src, dst, kind),
    )
    if cur.rowcount > 0:
        return
    edge_id = new_id(IdKind.SOFT_EDGE)
    conn.execute(
        """
        INSERT INTO soft_edges (
            id, workspace_id, src_qualified_name, dst_qualified_name,
            edge_kind, weight, observation_count, last_seen_at,
            created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (edge_id, workspace_id, src, dst, kind, weight_increment, now, now, "{}"),
    )


def list_soft_neighbors(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    src_qualified_name: str,
    edge_kinds: list[str] | None = None,
    limit: int = 20,
) -> list[SoftEdge]:
    sql = "SELECT * FROM soft_edges WHERE workspace_id = ? AND src_qualified_name = ? "
    params: list[object] = [workspace_id, src_qualified_name]
    if edge_kinds:
        placeholders = ", ".join("?" * len(edge_kinds))
        sql += f"AND edge_kind IN ({placeholders}) "
        params.extend(edge_kinds)
    sql += "ORDER BY weight DESC, last_seen_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_edge(r) for r in rows]
