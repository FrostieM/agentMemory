"""Hygiene gap check for theories (validation + evidence)."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.hygiene_models import (
    HygieneFinding,
    json_len,
    table_exists,
)


def find_theory_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not table_exists(conn, "theories"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, status, validation_criteria_json, experiment_plan,
               evidence_count, importance
        FROM theories
        WHERE workspace_id = ?
          AND status IN ('proposed', 'testing', 'supported', 'validated', 'rejected')
        ORDER BY importance DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    for row in rows:
        theory_id = str(row["id"])
        title = str(row["title"])
        status = str(row["status"])
        validation_count = json_len(row["validation_criteria_json"])
        if status != "rejected" and validation_count == 0 and not row["experiment_plan"]:
            findings.append(
                HygieneFinding(
                    kind="theory_without_validation",
                    severity="warning",
                    target_type="theory",
                    target_id=theory_id,
                    summary="Active theory has no validation criteria or experiment plan.",
                    details={"title": title, "status": status, "importance": row["importance"]},
                )
            )
        if int(row["evidence_count"]) == 0:
            findings.append(
                HygieneFinding(
                    kind="theory_without_evidence",
                    severity="warning",
                    target_type="theory",
                    target_id=theory_id,
                    summary="Theory has no attached evidence yet.",
                    details={"title": title, "status": status, "importance": row["importance"]},
                )
            )
    return findings
