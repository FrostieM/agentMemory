"""SQL operations for ``memory_state_snapshots``.

Distinct from research-dataset snapshots. These rows are produced
by ``ingestion/memory_state_snapshot_writer.py`` after walking the
workspace and computing per-kind counts + a small digest map.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.memory_state_snapshots import MemoryStateSnapshot


def _row_to_snapshot(row: sqlite3.Row) -> MemoryStateSnapshot:
    return MemoryStateSnapshot(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        taken_at=row["taken_at"],
        counts=_loads_dict_int(row["counts_json"]),
        digests=_loads_dict_str(row["digests_json"]),
        metadata=_loads_dict_any(row["metadata_json"]),
        created_at=row["created_at"],
    )


def _loads_dict_int(raw: str | None) -> dict[str, int]:
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in data.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _loads_dict_str(raw: str | None) -> dict[str, str]:
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _loads_dict_any(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def insert_state_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str,
    workspace_id: str,
    name: str,
    taken_at: str,
    counts: dict[str, int],
    digests: dict[str, str],
    metadata: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO memory_state_snapshots (
            id, workspace_id, name, taken_at, counts_json, digests_json,
            metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, name) DO UPDATE SET
            taken_at = excluded.taken_at,
            counts_json = excluded.counts_json,
            digests_json = excluded.digests_json,
            metadata_json = excluded.metadata_json
        """,
        (
            snapshot_id,
            workspace_id,
            name,
            taken_at,
            json.dumps(counts, sort_keys=True),
            json.dumps(digests, sort_keys=True),
            json.dumps(metadata, sort_keys=True),
            created_at,
        ),
    )
    conn.commit()


def get_state_snapshot(conn: sqlite3.Connection, snapshot_id: str) -> MemoryStateSnapshot | None:
    row = conn.execute(
        "SELECT * FROM memory_state_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def get_state_snapshot_by_name(
    conn: sqlite3.Connection, *, workspace_id: str, name: str
) -> MemoryStateSnapshot | None:
    row = conn.execute(
        "SELECT * FROM memory_state_snapshots WHERE workspace_id = ? AND name = ?",
        (workspace_id, name),
    ).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def list_state_snapshots(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit: int = 20,
) -> list[MemoryStateSnapshot]:
    rows = conn.execute(
        """
        SELECT * FROM memory_state_snapshots
        WHERE workspace_id = ?
        ORDER BY taken_at DESC
        LIMIT ?
        """,
        (workspace_id, max(1, int(limit))),
    ).fetchall()
    return [_row_to_snapshot(row) for row in rows]
