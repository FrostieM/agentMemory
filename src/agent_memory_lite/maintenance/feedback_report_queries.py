"""Result types + read queries for the usage-feedback report.

Split out of ``feedback_report.py`` so the orchestrator stays under
the SLOC ceiling. The dataclasses are reusable by tools that read the
report (CLI, dashboard).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FeedbackSourceSummary:
    source_type: str
    source_id: str
    feedback_count: int
    average_usefulness: float
    usefulness_sum: float
    helpful_count: int
    noisy_count: int
    latest_at: str | None
    sample_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "feedback_count": self.feedback_count,
            "average_usefulness": self.average_usefulness,
            "usefulness_sum": self.usefulness_sum,
            "helpful_count": self.helpful_count,
            "noisy_count": self.noisy_count,
            "latest_at": self.latest_at,
            "sample_queries": self.sample_queries,
        }


@dataclass(frozen=True, slots=True)
class UsageFeedbackReport:
    status: str
    workspace_id: str
    counts: dict[str, int]
    helpful_sources: list[FeedbackSourceSummary]
    noisy_sources: list[FeedbackSourceSummary]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "counts": self.counts,
            "helpful_sources": [item.to_dict() for item in self.helpful_sources],
            "noisy_sources": [item.to_dict() for item in self.noisy_sources],
            "warnings": self.warnings,
        }


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def sample_queries(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
    limit: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT query
        FROM memory_usage_feedback
        WHERE workspace_id = ?
          AND source_type = ?
          AND source_id = ?
          AND query <> ''
        GROUP BY query
        ORDER BY MAX(created_at) DESC
        LIMIT ?
        """,
        (workspace_id, source_type, source_id, limit),
    ).fetchall()
    return [str(row["query"]) for row in rows]


def per_source_summaries(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit: int,
    query_limit: int,
) -> list[FeedbackSourceSummary]:
    rows = conn.execute(
        """
        SELECT source_type,
               source_id,
               COUNT(*) AS feedback_count,
               AVG(usefulness) AS average_usefulness,
               SUM(usefulness) AS usefulness_sum,
               SUM(CASE WHEN usefulness >= 0.5 THEN 1 ELSE 0 END) AS helpful_count,
               SUM(CASE WHEN usefulness <= -0.5 THEN 1 ELSE 0 END) AS noisy_count,
               MAX(created_at) AS latest_at
        FROM memory_usage_feedback
        WHERE workspace_id = ?
        GROUP BY source_type, source_id
        ORDER BY ABS(AVG(usefulness)) DESC, COUNT(*) DESC, MAX(created_at) DESC
        LIMIT ?
        """,
        (workspace_id, limit),
    ).fetchall()
    return [
        FeedbackSourceSummary(
            source_type=str(row["source_type"]),
            source_id=str(row["source_id"]),
            feedback_count=int(row["feedback_count"] or 0),
            average_usefulness=float(row["average_usefulness"] or 0.0),
            usefulness_sum=float(row["usefulness_sum"] or 0.0),
            helpful_count=int(row["helpful_count"] or 0),
            noisy_count=int(row["noisy_count"] or 0),
            latest_at=str(row["latest_at"]) if row["latest_at"] else None,
            sample_queries=sample_queries(
                conn,
                workspace_id=workspace_id,
                source_type=str(row["source_type"]),
                source_id=str(row["source_id"]),
                limit=query_limit,
            ),
        )
        for row in rows
    ]
