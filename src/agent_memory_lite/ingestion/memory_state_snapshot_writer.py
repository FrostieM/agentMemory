"""Capture a memory state snapshot for ``workspace_id``.

Walks the same nine kinds the audit / archive surface already
covers, computes a per-row short hash for content-change detection,
and writes one ``memory_state_snapshots`` row. The diff helper is
in ``memory_state_snapshot_diff.py`` so this module stays focused on
capture + persistence.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from agent_memory_lite.models.memory_state_snapshots import MemoryStateSnapshot
from agent_memory_lite.repositories.memory_state_snapshots_repo import (
    get_state_snapshot,
    insert_state_snapshot,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

# (label, sql) pairs. The label becomes the prefix in the digest map
# (``"<label>:<id>"``) and the count key (``<label>_total``). Each
# query returns ``(id, fingerprint)`` rows; the fingerprint is hashed
# down to 12 hex chars to keep the digests JSON compact.
_KIND_QUERIES: list[tuple[str, str]] = [
    (
        "decision",
        "SELECT id, title || '|' || decision_text || '|' || status FROM decisions WHERE workspace_id = ?",
    ),
    (
        "theory",
        "SELECT id, title || '|' || claim || '|' || status FROM theories WHERE workspace_id = ?",
    ),
    (
        "experiment",
        "SELECT id, title || '|' || hypothesis || '|' || status FROM research_experiments WHERE workspace_id = ?",
    ),
    (
        "insight",
        "SELECT id, summary || '|' || status FROM research_insights WHERE workspace_id = ?",
    ),
    ("concept", "SELECT id, name || '|' || definition FROM domain_concepts WHERE workspace_id = ?"),
    (
        "snapshot",
        "SELECT id, snapshot_key || '|' || title FROM memory_snapshots WHERE workspace_id = ?",
    ),
    (
        "episode",
        "SELECT id, COALESCE(source_type, '') || '|' || COALESCE(raw_text, '') FROM episodes WHERE workspace_id = ?",
    ),
    (
        "behavior",
        "SELECT id, name || '|' || rule || '|' || CAST(active AS TEXT) FROM behavior_instructions WHERE workspace_id = ?",
    ),
    (
        "core_memory",
        "SELECT id, key || '|' || value || '|' || CAST(active AS TEXT) FROM core_memory WHERE workspace_id = ?",
    ),
    ("role", "SELECT id, name || '|' || purpose FROM agent_roles WHERE workspace_id = ?"),
    ("skill", "SELECT id, name || '|' || summary FROM agent_skills WHERE workspace_id = ?"),
    ("playbook", "SELECT id, name || '|' || goal FROM agent_playbooks WHERE workspace_id = ?"),
]


def _short_hash(payload: str) -> str:
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=6).hexdigest()


def _capture_kind(
    conn: sqlite3.Connection, *, label: str, sql: str, workspace_id: str
) -> tuple[int, dict[str, str]]:
    digests: dict[str, str] = {}
    rows = conn.execute(sql, (workspace_id,)).fetchall()
    for row in rows:
        row_id = row[0]
        fingerprint = "" if row[1] is None else str(row[1])
        digests[f"{label}:{row_id}"] = _short_hash(fingerprint)
    return len(rows), digests


def capture_state_snapshot(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> MemoryStateSnapshot:
    """Walk the workspace once and persist a ``MemoryStateSnapshot``."""
    snapshot_id = new_id(IdKind.MEMORY_STATE_SNAPSHOT)
    timestamp = iso_now()
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for label, sql in _KIND_QUERIES:
        count, kind_digests = _capture_kind(conn, label=label, sql=sql, workspace_id=workspace_id)
        counts[f"{label}_total"] = count
        digests.update(kind_digests)
    insert_state_snapshot(
        conn,
        snapshot_id=snapshot_id,
        workspace_id=workspace_id,
        name=(name or timestamp),
        taken_at=timestamp,
        counts=counts,
        digests=digests,
        metadata=metadata or {},
        created_at=timestamp,
    )
    saved = get_state_snapshot(conn, snapshot_id)
    if saved is None:
        raise RuntimeError(f"failed to persist state snapshot: {snapshot_id}")
    return saved
