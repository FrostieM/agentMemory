"""Promote durable, high-trust facts into core memory.

Each (`workspace_id`, `key`) pair has at most one active row. Writing a new
value for an existing key deactivates the prior one (kept for audit) and adds
a fresh active row.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.core_memory import CoreMemory, CoreMemoryIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.core_memory_repo import (
    deactivate_for_key,
    get_active_by_key,
    upsert_core_memory_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_core_memory(conn: sqlite3.Connection, payload: CoreMemoryIn) -> CoreMemory:
    timestamp = iso_now()
    new_core_id = new_id(IdKind.CORE_MEMORY)

    with with_tx(conn):
        deactivate_for_key(
            conn,
            workspace_id=payload.workspace_id,
            key=payload.key,
            timestamp=timestamp,
        )
        upsert_core_memory_row(
            conn,
            core_id=new_core_id,
            workspace_id=payload.workspace_id,
            key=payload.key,
            value=payload.value,
            source_episode_id=payload.source_episode_id,
            confidence=payload.confidence,
            importance=payload.importance,
            timestamp=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="promote_core_memory",
            target_type="core_memory",
            target_id=new_core_id,
            source_episode_id=payload.source_episode_id,
            after={"key": payload.key},
        )

    written = get_active_by_key(conn, payload.workspace_id, payload.key)
    assert written is not None
    return written
