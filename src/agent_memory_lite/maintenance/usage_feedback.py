"""Retrieval usage feedback used to gently tune future ranking."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class UsageFeedback:
    id: str
    workspace_id: str
    source_type: str
    source_id: str
    query: str
    usefulness: float
    task_id: str | None
    notes: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.id,
            "workspace_id": self.workspace_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "query": self.query,
            "usefulness": self.usefulness,
            "task_id": self.task_id,
            "notes": self.notes,
            "created_at": self.created_at,
        }


def _feedback_id(
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
    query: str,
    usefulness: float,
    task_id: str | None,
    created_at: str,
) -> str:
    raw = "|".join(
        [
            workspace_id,
            source_type,
            source_id,
            query,
            f"{usefulness:.4f}",
            task_id or "",
            created_at,
        ]
    )
    return "uf_" + hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()


def record_usage_feedback(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
    query: str,
    usefulness: float,
    task_id: str | None = None,
    notes: str = "",
    source: str = "agent_observed",
) -> UsageFeedback:
    if source_type not in {"chunk", "decision", "theory", "insight", "capability"}:
        raise ValueError(f"unsupported usage feedback source_type: {source_type!r}")
    usefulness = max(-1.0, min(1.0, float(usefulness)))
    created_at = iso_now()
    feedback_id = _feedback_id(
        workspace_id=workspace_id,
        source_type=source_type,
        source_id=source_id,
        query=query,
        usefulness=usefulness,
        task_id=task_id,
        created_at=created_at,
    )
    conn.execute(
        """
        INSERT INTO memory_usage_feedback (
            id, workspace_id, source_type, source_id, query, usefulness,
            task_id, notes, created_at, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feedback_id,
            workspace_id,
            source_type,
            source_id,
            query,
            usefulness,
            task_id,
            notes,
            created_at,
            source,
        ),
    )
    return UsageFeedback(
        id=feedback_id,
        workspace_id=workspace_id,
        source_type=source_type,
        source_id=source_id,
        query=query,
        usefulness=usefulness,
        task_id=task_id,
        notes=notes,
        created_at=created_at,
    )


def chunk_feedback_boosts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    chunk_ids: list[str],
) -> dict[str, float]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"""
        SELECT source_id,
               SUM(usefulness) AS usefulness_sum,
               COUNT(*) AS feedback_count
        FROM memory_usage_feedback
        WHERE workspace_id = ?
          AND source_type = 'chunk'
          AND source_id IN ({placeholders})
        GROUP BY source_id
        """,
        (workspace_id, *chunk_ids),
    ).fetchall()
    boosts: dict[str, float] = {}
    for row in rows:
        summed = float(row["usefulness_sum"] or 0.0)
        count = int(row["feedback_count"] or 0)
        # Keep feedback influential but never dominant: it can break ties and
        # demote known noise, but retrieval evidence still owns the ranking.
        boost = max(-0.12, min(0.18, 0.035 * summed + 0.005 * min(count, 5)))
        boosts[str(row["source_id"])] = boost
    return boosts
