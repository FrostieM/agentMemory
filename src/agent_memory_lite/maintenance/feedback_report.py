"""Summarize retrieval usage feedback for operators.

Result types and read queries live in ``feedback_report_queries.py``;
this module owns the orchestrator that decides helpful vs noisy and
emits the wire-shape report.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.feedback_report_queries import (
    FeedbackSourceSummary,
    UsageFeedbackReport,
    per_source_summaries,
    table_exists,
)

# Re-export so existing call sites keep working.
__all__ = [
    "FeedbackSourceSummary",
    "UsageFeedbackReport",
    "run_usage_feedback_report",
]


def run_usage_feedback_report(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit: int = 20,
    min_count: int = 2,
    noisy_threshold: float = -0.4,
    helpful_threshold: float = 0.4,
) -> UsageFeedbackReport:
    """Return a read-only summary of helpful/noisy memory retrieval feedback."""

    if not table_exists(conn, "memory_usage_feedback"):
        return UsageFeedbackReport(
            status="unknown",
            workspace_id=workspace_id,
            counts={"total": 0, "sources": 0, "helpful_sources": 0, "noisy_sources": 0},
            helpful_sources=[],
            noisy_sources=[],
            warnings=["memory_usage_feedback table is missing; run migrations"],
        )

    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM memory_usage_feedback WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()[0]
    )
    summaries = per_source_summaries(conn, workspace_id=workspace_id, limit=limit, query_limit=3)
    noisy_sources = [
        item
        for item in summaries
        if item.feedback_count >= min_count
        and (item.average_usefulness <= noisy_threshold or item.noisy_count > item.helpful_count)
    ]
    helpful_sources = [
        item
        for item in summaries
        if item.feedback_count >= min_count and item.average_usefulness >= helpful_threshold
    ]
    warnings = [
        f"{item.source_type}:{item.source_id} average_usefulness={item.average_usefulness:.3f}"
        for item in noisy_sources
    ]
    if total == 0:
        warnings.append("no usage feedback recorded yet")
    return UsageFeedbackReport(
        status="warning" if warnings else "ok",
        workspace_id=workspace_id,
        counts={
            "total": total,
            "sources": len(summaries),
            "helpful_sources": len(helpful_sources),
            "noisy_sources": len(noisy_sources),
        },
        helpful_sources=helpful_sources,
        noisy_sources=noisy_sources,
        warnings=warnings,
    )
