"""Batched last_retrieved_at writes for ranking-eligible kinds.

The retrieval pipeline calls ``mark_retrieved`` with the ids that survived
into the top-K of an envelope build. This module:

* writes ``last_retrieved_at = now()`` in a single batched UPDATE per kind,
* batches audit rows so the audit log doesn't grow once per query
  (per plan blind spot #5: emit at most one audit row per
  ``MEMORY_RETRIEVAL_AUDIT_BATCH_SIZE`` timestamp updates).

Pure-function ``decide_audit_emission`` is the audit-batching policy; it
is unit-tested independently of the database.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now

KIND_TO_TABLE: dict[str, str] = {
    "chunk": "chunks",
    "decision": "decisions",
    "theory": "theories",
    "concept": "domain_concepts",
}
SUPPORTED_KINDS: tuple[str, ...] = tuple(KIND_TO_TABLE.keys())


@dataclass(frozen=True, slots=True)
class RetrievalUpdate:
    kind: str
    ids: tuple[str, ...]


def decide_audit_emission(*, pending_count: int, batch_size: int) -> bool:
    """Audit emission policy: write a row when the running counter crosses
    `batch_size`. Pure function — caller tracks the running counter in
    workspace_meta or memory."""
    if batch_size <= 0:
        return True
    return pending_count >= batch_size


def _update_kind(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    ids: Iterable[str],
    now_iso: str,
) -> int:
    table = KIND_TO_TABLE.get(kind)
    if table is None:
        return 0
    id_list = [i for i in ids if i]
    if not id_list:
        return 0
    placeholders = ",".join("?" for _ in id_list)
    try:
        cursor = conn.execute(
            f"""
            UPDATE {table}
            SET last_retrieved_at = ?
            WHERE workspace_id = ? AND id IN ({placeholders})
            """,
            (now_iso, workspace_id, *id_list),
        )
    except sqlite3.OperationalError:
        # Pre-0022 schema (no last_retrieved_at column) — hub mode may
        # route here from a legacy DB. Treat as no-op: tracking is opt-in
        # observability, never required for correctness.
        return 0
    return int(cursor.rowcount or 0)


def mark_retrieved(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    updates: Iterable[RetrievalUpdate],
    audit_batch_size: int = 100,
) -> int:
    """Set last_retrieved_at on every (kind, id) in ``updates``.

    Returns the count of rows that were actually updated. Audit rows are
    batched: one ``retrieval.last_retrieved_updated`` audit row per call
    when the cumulative update count crosses ``audit_batch_size``.
    Updates only — never inserts; unknown ids are silently skipped.
    """
    now_iso = iso_now()
    total_updated = 0
    summary: dict[str, int] = {}
    for update in updates:
        rowcount = _update_kind(
            conn,
            workspace_id=workspace_id,
            kind=update.kind,
            ids=update.ids,
            now_iso=now_iso,
        )
        if rowcount > 0:
            summary[update.kind] = summary.get(update.kind, 0) + rowcount
            total_updated += rowcount
    if total_updated <= 0:
        return 0
    if decide_audit_emission(pending_count=total_updated, batch_size=audit_batch_size):
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="retrieval.last_retrieved_updated",
            target_type="workspace",
            target_id=workspace_id,
            after={"kinds": summary, "total": total_updated, "at": now_iso},
        )
    return total_updated
