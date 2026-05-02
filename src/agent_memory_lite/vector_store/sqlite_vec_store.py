"""sqlite-vec backed `VectorStore` (opt-in fallback).

Mirrors the LanceDB adapter's contract over a single SQLite database that loads
the `sqlite-vec` extension. If the extension fails to load (missing system
deps, mismatched architecture), the factory should fall back to LanceDB.

Per-namespace SQL helpers live in ``sqlite_vec_namespace.py``; this
module owns connection lifecycle and the ``VectorStore`` adapter shell.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from agent_memory_lite.vector_store import sqlite_vec_namespace as ns
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

    def _conn_or_open(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def upsert(self, namespace: str, rows: list[VectorRow]) -> None:
        if not rows:
            return
        conn = self._conn_or_open()
        dim = int(rows[0].vector.shape[0])
        ns.ensure_tables(conn, namespace, dim)
        for row in rows:
            ns.upsert_row(conn, namespace, row)
        conn.commit()

    def query(
        self,
        namespace: str,
        vector: np.ndarray,
        *,
        workspace_id: str,
        k: int = 10,
    ) -> list[VectorHit]:
        conn = self._conn_or_open()
        return ns.query_by_vector(conn, namespace, vector, workspace_id=workspace_id, k=k)

    def delete(self, namespace: str, ids: list[str]) -> int:
        if not ids:
            return 0
        conn = self._conn_or_open()
        deleted = ns.delete_ids(conn, namespace, ids)
        conn.commit()
        return deleted

    def drop_namespace(self, namespace: str) -> None:
        conn = self._conn_or_open()
        ns.drop_tables(conn, namespace)
        conn.commit()

    def count(self, namespace: str, *, workspace_id: str | None = None) -> int:
        return ns.count_rows(self._conn_or_open(), namespace, workspace_id=workspace_id)

    def list_ids(self, namespace: str, *, workspace_id: str | None = None) -> list[str]:
        return ns.list_ids(self._conn_or_open(), namespace, workspace_id=workspace_id)
