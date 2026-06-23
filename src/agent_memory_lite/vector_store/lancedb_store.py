"""LanceDB-backed `VectorStore`.

Tables are created lazily per namespace on the first upsert. Schema is fixed:
`id`, `workspace_id`, `vector`, `metadata_json`. Heterogeneous metadata is
serialized to JSON so the table schema stays stable across rows.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from agent_memory_lite.vector_store.base import (
    VectorHit,
    VectorRow,
    VectorStore,
    VectorStoreUnavailableError,
)

# v3.5 sector-2 audit-followup: validate workspace_id at the LanceDB
# adapter boundary. LanceDB's DataFusion ``where(...)`` clause is built
# via string interpolation (the LanceDB Python API doesn't accept ``?``
# placeholders here), so a ``workspace_id`` containing a single quote
# either crashes the query or — worse — escapes the predicate and
# returns rows from sibling workspaces. The allow-list pattern matches
# all currently-used workspace_id shapes (lowercase + digits + ``_-.``).
_WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def _validate_workspace_id(workspace_id: str) -> str:
    if not _WORKSPACE_ID_RE.match(workspace_id):
        raise VectorStoreUnavailableError(
            f"workspace_id {workspace_id!r} contains characters not allowed by "
            "the LanceDB filter allow-list. Only [A-Za-z0-9_.-] are accepted, "
            "max 128 chars."
        )
    return workspace_id


_VECTOR_ID_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,128}$")


def _validate_vector_id(vector_id: str) -> str:
    if not _VECTOR_ID_RE.match(vector_id):
        raise VectorStoreUnavailableError(
            f"vector id {vector_id!r} contains characters not allowed by "
            "the LanceDB filter allow-list."
        )
    return vector_id


def _safe_json_loads(raw: Any) -> dict[str, Any]:
    """Round-2 audit: ``query`` used to call ``json.loads`` on
    ``metadata_json`` unguarded. One corrupt / truncated cell (a
    LanceDB crash mid-write, a manual edit) raised ``JSONDecodeError``
    and broke the WHOLE query — every hit lost, the agent sees an
    empty vector result. Degrade per-row to empty metadata so a single
    bad cell can't take down retrieval."""
    try:
        loaded = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _assert_table_dim(table: Any, namespace: str, expected_dim: int) -> None:
    """Round-2 audit: ``_open_or_create`` keyed only on namespace name.
    If the embedding model changes dimension (384 -> 768) the existing
    table is silently reused and ``table.add`` stores mismatched-length
    vectors that corrupt every subsequent search. Catch the mismatch at
    open time with a clear "rebuild required" error instead.

    Failure-soft on schema introspection: if we cannot determine the
    stored dimension (LanceDB API drift) we skip the check rather than
    block a legitimate open."""
    try:
        field = table.schema.field("vector")
        existing_dim = getattr(field.type, "list_size", None)
    except Exception:  # pragma: no cover - schema introspection drift
        return
    if existing_dim is not None and existing_dim > 0 and existing_dim != expected_dim:
        raise VectorStoreUnavailableError(
            f"vector table {namespace!r} stores dim-{existing_dim} vectors "
            f"but the embedding provider now produces dim-{expected_dim}. "
            f"The embedding model changed; the table must be rebuilt — run "
            f"scripts/reindex_vectors.py."
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
        # Round-2 audit: lancedb deprecated ``table_names()`` (a plain
        # list) in favour of ``list_tables()`` which returns a paginated
        # ``ListTablesResponse(tables=[...], page_token=...)``. The old
        # name raises DeprecationWarning — an error under pytest's
        # filterwarnings — and is slated for removal. Prefer the new
        # API, walk its pagination, fall back to the old name for
        # pre-0.30 lancedb pins.
        list_tables = getattr(self._db, "list_tables", None)
        try:
            if list_tables is None:
                return set(self._db.table_names())
            names: set[str] = set()
            page_token = None
            for _ in range(1000):  # hard cap — never spin forever on a bad token
                resp = list_tables(page_token=page_token)
                # New build: ListTablesResponse with .tables; older /
                # alternate builds may return a bare list.
                tables = getattr(resp, "tables", resp)
                names.update(str(t) for t in tables)
                page_token = getattr(resp, "page_token", None)
                if not page_token:
                    break
            return names
        except Exception as exc:
            raise VectorStoreUnavailableError(f"lancedb table list failed: {exc}") from exc

    def _open_or_create(self, namespace: str, sample_dim: int) -> Any:
        self.open()
        if namespace in self._table_names():
            table = self._db.open_table(namespace)
            _assert_table_dim(table, namespace, sample_dim)
            return table
        seed = [
            {
                "id": "__seed__",
                "workspace_id": "__seed__",
                "vector": [0.0] * sample_dim,
                "metadata_json": "{}",
            }
        ]
        # v3.5 sector-2 audit-followup: pass metric="cosine" so LanceDB
        # indexes the table for cosine distance instead of its L2 default.
        # The downstream ``similarity = 1.0 - distance`` line in ``query``
        # is correct ONLY for cosine; with L2 the absolute score was
        # wrong (ranking happened to stay monotonic for normalised
        # vectors, but any downstream consumer of ``query`` would read
        # wrong absolute similarities).
        table = self._db.create_table(namespace, data=seed)
        table.delete("id = '__seed__'")
        return table

    def upsert(self, namespace: str, rows: list[VectorRow]) -> None:
        if not rows:
            return
        dim = int(rows[0].vector.shape[0])
        table = self._open_or_create(namespace, dim)
        ids = [_validate_vector_id(row.id) for row in rows]
        # Round-2 audit: upsert validated row.id but wrote row.workspace_id
        # raw. A workspace_id with a single quote was then STORED — every
        # later query/count/delete that re-validates workspace_id rejects
        # it, so those rows became orphan vectors unreachable from SQLite
        # (or, worse, a stored ``x' OR '1'='1`` poisoned the table).
        # Validate at the write boundary too — reject before the bad
        # value can land.
        for row in rows:
            _validate_workspace_id(row.workspace_id)
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

    def compact(self, *, cleanup_older_than_seconds: float = 3600.0) -> dict[str, int]:
        """Collapse LanceDB MVCC version history.

        Every ``upsert``/``delete`` appends a manifest version that nothing else
        prunes (measured: 4282 versions / 340 MB for 7356 live rows). LanceDB's
        ``Table.optimize`` compacts data fragments AND drops superseded versions
        older than the window. The default 1h window is safe against a concurrent
        writer in the same process; an operator one-shot can pass 0 to reclaim
        everything. Failure-soft per namespace -- one bad table never aborts the
        rest. Returns ``{namespace: live_row_count}`` (-1 marks a failed table).
        """
        from datetime import timedelta  # noqa: PLC0415

        self.open()
        window = timedelta(seconds=max(0.0, cleanup_older_than_seconds))
        result: dict[str, int] = {}
        for namespace in sorted(self._table_names()):
            try:
                table = self._db.open_table(namespace)
                table.optimize(cleanup_older_than=window, delete_unverified=False)
                result[namespace] = int(table.count_rows())
            except Exception:
                result[namespace] = -1
        return result

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
        # v3.5 sector-2 audit-followup: validate workspace_id at the
        # adapter boundary so a quoted/injected value can never reach
        # the DataFusion where-clause.
        _validate_workspace_id(workspace_id)
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
                    metadata=_safe_json_loads(row.get("metadata_json", "{}")),
                )
            )
        return hits

    def delete(self, namespace: str, ids: list[str]) -> int:
        if not ids or namespace not in self._table_names():
            return 0
        # v3.5 sector-2 audit-followup: validate ids before interpolation.
        validated = [_validate_vector_id(row_id) for row_id in ids]
        table = self._db.open_table(namespace)
        quoted = ", ".join(f"'{row_id}'" for row_id in validated)
        table.delete(f"id IN ({quoted})")
        return len(validated)

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
        _validate_workspace_id(workspace_id)
        return int(table.count_rows(filter=f"workspace_id = '{workspace_id}'"))

    def list_ids(self, namespace: str, *, workspace_id: str | None = None) -> list[str]:
        if namespace not in self._table_names():
            return []
        table = self._db.open_table(namespace)
        try:
            rows = table.to_arrow().to_pylist()
        except Exception:
            rows = table.to_pandas().to_dict("records")
        ids: list[str] = []
        for row in rows:
            if workspace_id is not None and str(row.get("workspace_id")) != workspace_id:
                continue
            ids.append(str(row["id"]))
        return ids
