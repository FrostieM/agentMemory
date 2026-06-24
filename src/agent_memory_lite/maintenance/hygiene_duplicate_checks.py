"""Duplicate and low-signal object checks for memory hygiene."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from agent_memory_lite.maintenance.hygiene_duplicate_episodes import (
    find_recent_duplicate_episode_gaps,
)
from agent_memory_lite.maintenance.hygiene_models import (
    HygieneFinding,
    table_exists,
)
from agent_memory_lite.utils.insight_filters import is_low_signal_insight

__all__ = [
    "find_duplicate_decision_gaps",
    "find_low_signal_insight_gaps",
    "find_orphan_or_closed_task_plan_step_gaps",
    "find_recent_duplicate_episode_gaps",
]


def _norm(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def find_duplicate_decision_gaps(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[HygieneFinding]:
    if not table_exists(conn, "decisions"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, source_episode_id, updated_at
        FROM decisions
        WHERE workspace_id = ? AND status = 'active' AND COALESCE(title, '') != ''
        ORDER BY lower(title), updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[_norm(str(row["title"]))].append(row)
    findings: list[HygieneFinding] = []
    for title_key, items in groups.items():
        if len(items) < 2:
            continue
        findings.append(
            HygieneFinding(
                kind="duplicate_active_decision",
                severity="warning",
                target_type="decision",
                target_id=str(items[0]["id"]),
                summary="Multiple active decisions share the same normalized title.",
                details={
                    "title": title_key,
                    "duplicate_ids": [str(item["id"]) for item in items],
                    "source_episode_ids": [str(item["source_episode_id"] or "") for item in items],
                },
            )
        )
    return findings


def find_low_signal_insight_gaps(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[HygieneFinding]:
    if not table_exists(conn, "insights"):
        return []
    rows = conn.execute(
        """
        SELECT id, insight_type, summary, gist, tags_json, status
        FROM insights
        WHERE workspace_id = ?
          AND status IN ('candidate', 'new', 'accepted')
        ORDER BY updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    low_signal_rows = [
        row for row in rows if is_low_signal_insight(row["summary"], row["gist"], row["tags_json"])
    ]
    return [
        HygieneFinding(
            kind="low_signal_insight",
            severity="warning",
            target_type="insight",
            target_id=str(row["id"]),
            summary="Low-signal auto-consolidation insight should be archived or suppressed.",
            details={
                "insight_type": row["insight_type"],
                "status": row["status"],
                "summary": row["summary"],
            },
        )
        for row in low_signal_rows
    ]


def find_orphan_or_closed_task_plan_step_gaps(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[HygieneFinding]:
    if not table_exists(conn, "plan_steps") or not table_exists(conn, "tasks"):
        return []
    rows = conn.execute(
        """
        SELECT
            ps.id,
            ps.task_id,
            ps.status AS plan_status,
            ps.title,
            t.id AS task_row_id,
            t.status AS task_status
        FROM plan_steps ps
        LEFT JOIN tasks t
          ON t.workspace_id = ps.workspace_id
         AND t.task_id = ps.task_id
        WHERE ps.workspace_id = ?
          AND ps.valid_to IS NULL
          AND ps.status IN ('active', 'pending', 'blocked')
          AND (t.id IS NULL OR t.status NOT IN ('active', 'in_progress'))
        ORDER BY ps.task_id, ps.rank
        """,
        (workspace_id,),
    ).fetchall()
    return [
        HygieneFinding(
            kind="stale_open_plan_step",
            severity="warning",
            target_type="plan_step",
            target_id=str(row["id"]),
            summary="Open plan step belongs to a missing or closed task.",
            details={
                "task_id": row["task_id"],
                "plan_status": row["plan_status"],
                "task_row_id": row["task_row_id"],
                "task_status": row["task_status"],
                "title": row["title"],
            },
        )
        for row in rows
    ]
