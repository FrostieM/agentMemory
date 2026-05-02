"""Per-namespace SQL helpers for the sqlite-vec adapter.

Split out of ``sqlite_vec_store.py`` so the store class stays under
the SLOC ceiling and the SQL strings sit in one place. Each function
takes the open ``sqlite3.Connection`` and the namespace name; the
store handles connection lifecycle and commits.
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np

from agent_memory_lite.vector_store.base import VectorHit, VectorRow


def ensure_tables(conn: sqlite3.Connection, namespace: str, dim: int) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {namespace}_meta (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{{}}'
        )
        """
    )
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {namespace}_vec USING vec0(
            id TEXT PRIMARY KEY,
            vector float[{dim}]
        )
        """
    )


def upsert_row(conn: sqlite3.Connection, namespace: str, row: VectorRow) -> None:
    conn.execute(f"DELETE FROM {namespace}_meta WHERE id = ?", (row.id,))
    conn.execute(f"DELETE FROM {namespace}_vec WHERE id = ?", (row.id,))
    conn.execute(
        f"INSERT INTO {namespace}_meta (id, workspace_id, metadata_json) VALUES (?, ?, ?)",
        (
            row.id,
            row.workspace_id,
            json.dumps(row.metadata, sort_keys=True, default=str),
        ),
    )
    conn.execute(
        f"INSERT INTO {namespace}_vec (id, vector) VALUES (?, ?)",
        (row.id, row.vector.astype(np.float32).tobytes()),
    )


def query_by_vector(
    conn: sqlite3.Connection,
    namespace: str,
    vector: np.ndarray,
    *,
    workspace_id: str,
    k: int,
) -> list[VectorHit]:
    try:
        cur = conn.execute(
            f"""
            SELECT m.id, m.workspace_id, m.metadata_json,
                   vec_distance_cosine(v.vector, ?) AS distance
            FROM {namespace}_vec v
            JOIN {namespace}_meta m ON m.id = v.id
            WHERE m.workspace_id = ?
            ORDER BY distance ASC
            LIMIT ?
            """,
            (vector.astype(np.float32).tobytes(), workspace_id, k),
        )
    except sqlite3.OperationalError:
        return []
    return [
        VectorHit(
            id=str(row[0]),
            workspace_id=str(row[1]),
            score=1.0 - float(row[3]),
            metadata=json.loads(row[2] or "{}"),
        )
        for row in cur.fetchall()
    ]


def delete_ids(conn: sqlite3.Connection, namespace: str, ids: list[str]) -> int:
    marks = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM {namespace}_vec WHERE id IN ({marks})", tuple(ids))
    cur = conn.execute(f"DELETE FROM {namespace}_meta WHERE id IN ({marks})", tuple(ids))
    return int(cur.rowcount)


def drop_tables(conn: sqlite3.Connection, namespace: str) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {namespace}_vec")
    conn.execute(f"DROP TABLE IF EXISTS {namespace}_meta")


def count_rows(conn: sqlite3.Connection, namespace: str, *, workspace_id: str | None) -> int:
    try:
        if workspace_id is None:
            row = conn.execute(f"SELECT COUNT(*) FROM {namespace}_meta").fetchone()
        else:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {namespace}_meta WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def list_ids(conn: sqlite3.Connection, namespace: str, *, workspace_id: str | None) -> list[str]:
    try:
        if workspace_id is None:
            rows = conn.execute(f"SELECT id FROM {namespace}_meta").fetchall()
        else:
            rows = conn.execute(
                f"SELECT id FROM {namespace}_meta WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [str(row[0]) for row in rows]
