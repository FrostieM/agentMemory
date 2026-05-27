"""SQL operations for canonical ``snapshots``."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.research import MemorySnapshot
from agent_memory_lite.repositories.research_helpers import (
    contains_all,
    row_to_snapshot,
    tokens_from,
)
from agent_memory_lite.utils.sql_filters import date_range_clause


def upsert_snapshot_row(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str,
    workspace_id: str,
    snapshot_key: str,
    title: str,
    source: str,
    db_path: str | None,
    duckdb_path: str | None,
    parquet_dir: str | None,
    window_start: str | None,
    window_end: str | None,
    build_sha: str | None,
    build_branch: str | None,
    build_time: str | None,
    remote_host: str | None,
    table_counts: dict[str, int],
    total_rows: int,
    metadata: dict[str, Any],
    source_episode_id: str | None,
    created_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO snapshots (
            id, workspace_id, snapshot_key, title, source_label, db_path,
            duckdb_path, parquet_dir, window_start, window_end, build_sha,
            build_branch, build_time, remote_host, table_counts_json, total_rows,
            metadata_json, source_episode_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, snapshot_key) DO UPDATE SET
            title = excluded.title,
            source_label = excluded.source_label,
            db_path = excluded.db_path,
            duckdb_path = excluded.duckdb_path,
            parquet_dir = excluded.parquet_dir,
            window_start = excluded.window_start,
            window_end = excluded.window_end,
            build_sha = excluded.build_sha,
            build_branch = excluded.build_branch,
            build_time = excluded.build_time,
            remote_host = excluded.remote_host,
            table_counts_json = excluded.table_counts_json,
            total_rows = excluded.total_rows,
            metadata_json = excluded.metadata_json,
            source_episode_id = excluded.source_episode_id,
            updated_at = excluded.updated_at
        """,
        (
            snapshot_id,
            workspace_id,
            snapshot_key,
            title,
            source,
            db_path,
            duckdb_path,
            parquet_dir,
            window_start,
            window_end,
            build_sha,
            build_branch,
            build_time,
            remote_host,
            json.dumps(table_counts, sort_keys=True),
            total_rows,
            json.dumps(metadata, sort_keys=True),
            source_episode_id,
            created_at,
            updated_at,
        ),
    )


def get_snapshot(conn: sqlite3.Connection, snapshot_id: str) -> MemorySnapshot | None:
    row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return row_to_snapshot(row) if row is not None else None


def get_snapshot_by_key(
    conn: sqlite3.Connection, *, workspace_id: str, snapshot_key: str
) -> MemorySnapshot | None:
    row = conn.execute(
        "SELECT * FROM snapshots WHERE workspace_id = ? AND snapshot_key = ?",
        (workspace_id, snapshot_key),
    ).fetchone()
    return row_to_snapshot(row) if row is not None else None


def _snapshot_text(snapshot: MemorySnapshot) -> str:
    return " ".join(
        [
            snapshot.snapshot_key,
            snapshot.title,
            snapshot.source,
            snapshot.db_path or "",
            snapshot.duckdb_path or "",
            snapshot.parquet_dir or "",
            snapshot.remote_host or "",
            " ".join(snapshot.table_counts),
        ]
    )


def list_snapshots(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    limit: int = 10,
    since: str | None = None,
    until: str | None = None,
) -> list[MemorySnapshot]:
    extra_sql, extra_params = date_range_clause(since=since, until=until)
    rows = conn.execute(
        f"SELECT * FROM snapshots WHERE workspace_id = ? {extra_sql} ORDER BY updated_at DESC",
        (workspace_id, *extra_params),
    ).fetchall()
    tokens = tokens_from(query)
    snapshots = [row_to_snapshot(row) for row in rows]
    snapshots = [item for item in snapshots if contains_all(_snapshot_text(item), tokens)]
    return snapshots[:limit]
