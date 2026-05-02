"""Per-kind hygiene gap checks (candidates / insights / decisions).

Experiment gaps live in ``hygiene_experiment_check.py``; theory gaps
in ``hygiene_theory_check.py``. Both are re-exported so existing
import sites keep working.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.maintenance.hygiene_experiment_check import find_experiment_gaps
from agent_memory_lite.maintenance.hygiene_models import (
    HygieneFinding,
    parse_iso,
    table_exists,
)
from agent_memory_lite.maintenance.hygiene_theory_check import find_theory_gaps

__all__ = [
    "find_decision_gaps",
    "find_experiment_gaps",
    "find_insight_gaps",
    "find_stale_candidates",
    "find_theory_gaps",
]


def find_stale_candidates(
    conn: sqlite3.Connection, *, workspace_id: str, stale_days: int
) -> list[HygieneFinding]:
    if not table_exists(conn, "memory_candidates"):
        return []
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    rows = conn.execute(
        """
        SELECT id, kind, subject, updated_at
        FROM memory_candidates
        WHERE workspace_id = ? AND status = 'new'
        ORDER BY updated_at
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    for row in rows:
        updated = parse_iso(str(row["updated_at"]))
        if updated is None or updated >= cutoff:
            continue
        findings.append(
            HygieneFinding(
                kind="stale_candidate",
                severity="warning",
                target_type="memory_candidate",
                target_id=str(row["id"]),
                summary="Candidate remained new past the review window.",
                details={
                    "candidate_kind": row["kind"],
                    "subject": row["subject"],
                    "updated_at": row["updated_at"],
                    "stale_days": stale_days,
                },
            )
        )
    return findings


def find_insight_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not table_exists(conn, "research_insights"):
        return []
    rows = conn.execute(
        """
        SELECT id, insight_type, summary, status, target_type, target_id
        FROM research_insights
        WHERE workspace_id = ? AND status IN ('new', 'accepted')
          AND (COALESCE(target_type, '') = '' OR COALESCE(target_id, '') = '')
        ORDER BY updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [
        HygieneFinding(
            kind="unlinked_insight",
            severity="warning",
            target_type="research_insight",
            target_id=str(row["id"]),
            summary="Active insight is not linked to a target object.",
            details={
                "insight_type": row["insight_type"],
                "status": row["status"],
                "summary": row["summary"],
            },
        )
        for row in rows
    ]


def find_decision_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not table_exists(conn, "decisions"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, rationale, source_episode_id, importance
        FROM decisions
        WHERE workspace_id = ? AND status = 'active' AND importance >= 0.8
          AND COALESCE(rationale, '') = ''
          AND COALESCE(source_episode_id, '') = ''
        ORDER BY importance DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [
        HygieneFinding(
            kind="weak_decision_provenance",
            severity="warning",
            target_type="decision",
            target_id=str(row["id"]),
            summary="Important active decision lacks both rationale and source episode.",
            details={
                "title": row["title"],
                "has_rationale": bool(row["rationale"]),
                "has_source_episode": bool(row["source_episode_id"]),
                "importance": row["importance"],
            },
        )
        for row in rows
    ]
