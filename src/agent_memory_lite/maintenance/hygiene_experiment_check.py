"""Hygiene gap check for open experiments."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.maintenance.hygiene_models import (
    HygieneFinding,
    json_len,
    parse_iso,
    table_exists,
)


def find_experiment_gaps(
    conn: sqlite3.Connection, *, workspace_id: str, stale_days: int
) -> list[HygieneFinding]:
    if not table_exists(conn, "experiments"):
        return []
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    rows = conn.execute(
        """
        SELECT id, title, status, priority, due_at, updated_at, success_criteria_json
        FROM experiments
        WHERE workspace_id = ? AND status IN ('planned', 'running', 'blocked')
        ORDER BY priority DESC, updated_at
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    now = datetime.now(UTC)
    for row in rows:
        experiment_id = str(row["id"])
        title = str(row["title"])
        due_at = parse_iso(row["due_at"])
        updated_at = parse_iso(str(row["updated_at"]))
        if due_at is not None and due_at < now:
            findings.append(
                HygieneFinding(
                    kind="overdue_experiment",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment is past due.",
                    details={"title": title, "status": row["status"], "due_at": row["due_at"]},
                )
            )
        if updated_at is not None and updated_at < cutoff:
            findings.append(
                HygieneFinding(
                    kind="stale_open_experiment",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment has not been updated within the hygiene window.",
                    details={
                        "title": title,
                        "status": row["status"],
                        "updated_at": row["updated_at"],
                        "stale_days": stale_days,
                    },
                )
            )
        if json_len(row["success_criteria_json"]) == 0:
            findings.append(
                HygieneFinding(
                    kind="experiment_without_success_criteria",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment has no explicit success criteria.",
                    details={"title": title, "status": row["status"], "priority": row["priority"]},
                )
            )
    return findings
