"""Stale fact compaction.

Facts whose `valid_to` is older than `max_age_days` are tagged in metadata
(`stale=true`) so retrieval can downrank them and audits can show what was
trimmed. We do NOT delete them — historical mode still surfaces the timeline.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now, now

DEFAULT_AGE_DAYS = 60


@dataclass(frozen=True, slots=True)
class StaleFactStats:
    stale_count: int
    cutoff: str


def archive_stale_facts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    max_age_days: int = DEFAULT_AGE_DAYS,
) -> StaleFactStats:
    cutoff = (now() - timedelta(days=max_age_days)).isoformat()
    rows = conn.execute(
        """
        SELECT id, metadata_json FROM facts
        WHERE workspace_id = ? AND valid_to IS NOT NULL AND valid_to < ?
        """,
        (workspace_id, cutoff),
    ).fetchall()
    if not rows:
        return StaleFactStats(stale_count=0, cutoff=cutoff)

    with with_tx(conn):
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if metadata.get("stale") is True:
                continue
            metadata["stale"] = True
            metadata["archived_at"] = iso_now()
            conn.execute(
                "UPDATE facts SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, sort_keys=True), row["id"]),
            )
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="compact_archive_stale_facts",
            target_type="fact",
            target_id="bulk",
            after={"archived": len(rows), "cutoff": cutoff},
        )

    return StaleFactStats(stale_count=len(rows), cutoff=cutoff)
