"""LanceDB-backed `VectorStore`.

Tables are created lazily per namespace on the first upsert. Schema is fixed:
`id`, `workspace_id`, `vector`, `metadata_json`. Heterogeneous metadata is
serialized to JSON so the table schema stays stable across rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from agent_memory_lite.vector_store.base import (
    VectorHit,
    VectorRow,
    VectorStore,
    VectorStoreUnavailableError,
)


class LanceDBStore(VectorStore):
    backend = "lancedb"

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: Any = None

    def open(self) -> None:
        if self._db is not None:
            return
        try:
            import lancedb  # noqa: PLC0415
        except ImportError as exc:
            raise VectorStoreUnavailableError(
                "lancedb is not installed; install with `pip install -e .`"
            ) from exc
        self._db_path.mkdir(parents=True, exist_ok=True)
        try:
            self._db = lancedb.connect(str(self._db_path))
        except Exception as exc:
            raise VectorStoreUnavailableError(
                f"failed to connect to lancedb at {self._db_path}: {exc}"
            ) from exc

    def close(self) -> None:
        self._db = None

    def _table_names(self) -> set[str]:
        self.open()
        try:
            return set(self._db.table_names())
        except Exception as exc:
            raise VectorStoreUnavailableError(f"lancedb table_names failed: {exc}") from exc

    def _open_or_create(self, namespace: str, sample_dim: int) -> Any:
        self.open()
        if namespace in self._table_names():
            return self._db.open_table(namespace)
        seed = [
            {
                "id": "__seed__",
                "workspace_id": "__seed__",
                "vector": [0.0] * sample_dim,
                "metadata_json": "{}",
            }
        ]
        table = self._db.create_table(namespace, data=seed)
        table.delete("id = '__seed__'")
        return table

    def upsert(self, namespace: str, rows: list[VectorRow]) -> None:
        if not rows:
            return
        dim = int(rows[0].vector.shape[0])
        table = self._open_or_create(namespace, dim)
        ids = [row.id for row in rows]
        if ids:
            quoted = ", ".join(f"'{row_id}'" for row_id in ids)
            table.delete(f"id IN ({quoted})")
        records = [
            {
                "id": row.id,
                "workspace_id": row.workspace_id,
                "vector": row.vector.astype(np.float32).tolist(),
                "metadata_json": json.dumps(row.metadata, sort_keys=True, default=str),
            }
            for row in rows
        ]
        table.add(records)

    def query(
        self,
        namespace: str,
        vector: np.ndarray,
        *,
        workspace_id: str,
        k: int = 10,
    ) -> list[VectorHit]:
        if namespace not in self._table_names():
            return []
        table = self._db.open_table(namespace)
        results = (
            table.search(vector.astype(np.float32).tolist())
            .where(f"workspace_id = '{workspace_id}'")
            .limit(k)
            .to_list()
        )
        hits: list[VectorHit] = []
        for row in results:
            distance = float(row.get("_distance", 0.0))
            similarity = 1.0 - distance
            hits.append(
                VectorHit(
                    id=str(row["id"]),
                    workspace_id=str(row["workspace_id"]),
                    score=similarity,
                    metadata=json.loads(row.get("metadata_json", "{}")),
                )
            )
        return hits

    def delete(self, namespace: str, ids: list[str]) -> int:
        if not ids or namespace not in self._table_names():
            return 0
        table = self._db.open_table(namespace)
        quoted = ", ".join(f"'{row_id}'" for row_id in ids)
        table.delete(f"id IN ({quoted})")
        return len(ids)

    def drop_namespace(self, namespace: str) -> None:
        self.open()
        if namespace in self._table_names():
            self._db.drop_table(namespace)

    def count(self, namespace: str, *, workspace_id: str | None = None) -> int:
        if namespace not in self._table_names():
            return 0
        table = self._db.open_table(namespace)
        if workspace_id is None:
            return int(table.count_rows())
        return int(table.count_rows(filter=f"workspace_id = '{workspace_id}'"))
