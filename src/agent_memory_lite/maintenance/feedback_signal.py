"""Feedback signal observability — read-only summary helpers.

Used by ``/health`` (signal density + self-loop ratio) and the
``/memory/feedback_summary`` endpoint (per-source aggregate). Pure read.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class FeedbackSignalSummary:
    enabled: bool
    total_rows: int
    unique_sources: int
    self_loop_ratio: float
    last_feedback_at: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FeedbackSourceSummary:
    source_type: str
    source_id: str
    feedback_count: int
    usefulness_avg: float
    usefulness_sum: float
    last_feedback_at: str
    first_feedback_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def feedback_signal_summary(
    conn: sqlite3.Connection, *, workspace_id: str, enabled: bool
) -> FeedbackSignalSummary:
    """Workspace-wide rollup. Cheap (single GROUP BY across the feedback table)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT source_id) AS uniques,
               SUM(CASE WHEN source = 'self_loop' THEN 1 ELSE 0 END) AS self_loops,
               MAX(created_at) AS last_at
        FROM memory_usage_feedback
        WHERE workspace_id = ?
        """,
        (workspace_id,),
    ).fetchone()
    total = int(row["total"] or 0)
    self_loops = int(row["self_loops"] or 0)
    return FeedbackSignalSummary(
        enabled=enabled,
        total_rows=total,
        unique_sources=int(row["uniques"] or 0),
        self_loop_ratio=(self_loops / total) if total else 0.0,
        last_feedback_at=str(row["last_at"]) if row["last_at"] else None,
    )


def feedback_per_source_summary(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    source_type: str | None = None,
    limit: int = 50,
) -> list[FeedbackSourceSummary]:
    """Per-source aggregate, ordered by recency. Reads the migration 0020 view."""
    if source_type is not None:
        rows = conn.execute(
            """
            SELECT source_type, source_id, feedback_count, usefulness_avg,
                   usefulness_sum, last_feedback_at, first_feedback_at
            FROM memory_usage_feedback_summary
            WHERE workspace_id = ? AND source_type = ?
            ORDER BY last_feedback_at DESC
            LIMIT ?
            """,
            (workspace_id, source_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT source_type, source_id, feedback_count, usefulness_avg,
                   usefulness_sum, last_feedback_at, first_feedback_at
            FROM memory_usage_feedback_summary
            WHERE workspace_id = ?
            ORDER BY last_feedback_at DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    return [
        FeedbackSourceSummary(
            source_type=str(row["source_type"]),
            source_id=str(row["source_id"]),
            feedback_count=int(row["feedback_count"] or 0),
            usefulness_avg=float(row["usefulness_avg"] or 0.0),
            usefulness_sum=float(row["usefulness_sum"] or 0.0),
            last_feedback_at=str(row["last_feedback_at"] or ""),
            first_feedback_at=str(row["first_feedback_at"] or ""),
        )
        for row in rows
    ]
