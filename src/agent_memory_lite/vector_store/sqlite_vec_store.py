"""sqlite-vec backed `VectorStore` (opt-in fallback).

Mirrors the LanceDB adapter's contract over a single SQLite database that loads
the `sqlite-vec` extension. If the extension fails to load (missing system
deps, mismatched architecture), the factory should fall back to LanceDB.

Tables are created lazily per namespace and keyed by `id`. Cosine similarity is
returned via `vec_distance_cosine` (lower = closer); we convert to similarity in
[-1, 1] for parity with the LanceDB adapter.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from agent_memory_lite.vector_store.base import (
    VectorHit,
    VectorRow,
    VectorStore,
    VectorStoreUnavailableError,
)


class SqliteVecStore(VectorStore):
    backend = "sqlite_vec"

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._dim: int | None = None

    def open(self) -> None:
        if self._conn is not None:
            return
        try:
            import sqlite_vec  # noqa: PLC0415
        except ImportError as exc:
            raise VectorStoreUnavailableError(
                "sqlite-vec is not installed; pip install -e '.[sqlite-vec]'"
            ) from exc
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        except sqlite3.OperationalError as exc:
            raise VectorStoreUnavailableError(
                f"failed to load sqlite-vec extension: {exc}"
            ) from exc
        finally:
            conn.enable_load_extension(False)
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure_table(self, namespace: str, dim: int) -> None:
        assert self._conn is not None
        self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {namespace}_meta (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{{}}'
            )
            """
        )
        self._conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {namespace}_vec USING vec0(
                id TEXT PRIMARY KEY,
                vector float[{dim}]
            )
            """
        )

    def upsert(self, namespace: str, rows: list[VectorRow]) -> None:
        if not rows:
            return
        self.open()
        assert self._conn is not None
        dim = int(rows[0].vector.shape[0])
        self._ensure_table(namespace, dim)
        for row in rows:
            self._conn.execute(f"DELETE FROM {namespace}_meta WHERE id = ?", (row.id,))
            self._conn.execute(f"DELETE FROM {namespace}_vec WHERE id = ?", (row.id,))
            self._conn.execute(
                f"INSERT INTO {namespace}_meta (id, workspace_id, metadata_json) VALUES (?, ?, ?)",
                (
                    row.id,
                    row.workspace_id,
                    json.dumps(row.metadata, sort_keys=True, default=str),
                ),
            )
            self._conn.execute(
                f"INSERT INTO {namespace}_vec (id, vector) VALUES (?, ?)",
                (row.id, row.vector.astype(np.float32).tobytes()),
            )
        self._conn.commit()

    def query(
        self,
        namespace: str,
        vector: np.ndarray,
        *,
        workspace_id: str,
        k: int = 10,
    ) -> list[VectorHit]:
        self.open()
        assert self._conn is not None
        try:
            cur = self._conn.execute(
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

    def delete(self, namespace: str, ids: list[str]) -> int:
        if not ids:
            return 0
        self.open()
        assert self._conn is not None
        marks = ",".join("?" * len(ids))
        self._conn.execute(f"DELETE FROM {namespace}_vec WHERE id IN ({marks})", tuple(ids))
        cur = self._conn.execute(f"DELETE FROM {namespace}_meta WHERE id IN ({marks})", tuple(ids))
        self._conn.commit()
        return int(cur.rowcount)

    def drop_namespace(self, namespace: str) -> None:
        self.open()
        assert self._conn is not None
        self._conn.execute(f"DROP TABLE IF EXISTS {namespace}_vec")
        self._conn.execute(f"DROP TABLE IF EXISTS {namespace}_meta")
        self._conn.commit()

    def count(self, namespace: str, *, workspace_id: str | None = None) -> int:
        self.open()
        assert self._conn is not None
        try:
            if workspace_id is None:
                row = self._conn.execute(f"SELECT COUNT(*) FROM {namespace}_meta").fetchone()
            else:
                row = self._conn.execute(
                    f"SELECT COUNT(*) FROM {namespace}_meta WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0
