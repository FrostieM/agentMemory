"""Register memory snapshots."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.research import MemorySnapshot, MemorySnapshotIn
from agent_memory_lite.redaction import redact
from agent_memory_lite.redaction.payload import redact_freetext_fields
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.research_repo import (
    get_snapshot_by_key,
    upsert_snapshot_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def register_snapshot(conn: sqlite3.Connection, payload: MemorySnapshotIn) -> MemorySnapshot:
    snapshot_id = new_id(IdKind.MEMORY_SNAPSHOT)
    timestamp = iso_now()
    # global-audit round-A: the snapshot writer bypassed write_canonical's redaction
    # chokepoint, persisting title + metadata cleartext (they are returned to users via
    # the research API). Redact the free-text fields here, matching the experiment
    # writer, to honour the "Never store secrets" invariant.
    safe_title = redact(payload.title).text if payload.title else payload.title
    safe_metadata = (
        redact_freetext_fields(payload.metadata) if payload.metadata else payload.metadata
    )
    with with_tx(conn):
        upsert_snapshot_row(
            conn,
            snapshot_id=snapshot_id,
            workspace_id=payload.workspace_id,
            snapshot_key=payload.snapshot_key,
            title=safe_title,
            source=payload.source,
            db_path=payload.db_path,
            duckdb_path=payload.duckdb_path,
            parquet_dir=payload.parquet_dir,
            window_start=payload.window_start,
            window_end=payload.window_end,
            build_sha=payload.build_sha,
            build_branch=payload.build_branch,
            build_time=payload.build_time,
            remote_host=payload.remote_host,
            table_counts=payload.table_counts,
            total_rows=payload.total_rows,
            metadata=safe_metadata,
            source_episode_id=payload.source_episode_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        stored = get_snapshot_by_key(
            conn,
            workspace_id=payload.workspace_id,
            snapshot_key=payload.snapshot_key,
        )
        assert stored is not None
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="register_snapshot",
            target_type="memory_snapshot",
            target_id=stored.id,
            source_episode_id=payload.source_episode_id,
            after={
                "snapshot_key": payload.snapshot_key,
                "total_rows": payload.total_rows,
                "duckdb_path": payload.duckdb_path,
            },
        )
    snapshot = get_snapshot_by_key(
        conn,
        workspace_id=payload.workspace_id,
        snapshot_key=payload.snapshot_key,
    )
    assert snapshot is not None
    return snapshot
