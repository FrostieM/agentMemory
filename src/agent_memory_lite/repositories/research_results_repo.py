"""SQL operations for ``experiment_results``."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import TheoryEvidenceKind
from agent_memory_lite.models.research import ExperimentResult
from agent_memory_lite.repositories.research_helpers import row_to_result


def insert_experiment_result_row(
    conn: sqlite3.Connection,
    *,
    result_id: str,
    workspace_id: str,
    experiment_id: str,
    theory_id: str | None,
    kind: TheoryEvidenceKind,
    summary: str,
    metrics: dict[str, Any],
    artifact_path: str | None,
    confidence: float,
    observed_at: str,
    source_episode_id: str | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO experiment_results (
            id, workspace_id, experiment_id, theory_id, kind, summary,
            metrics_json, artifact_path, confidence, observed_at,
            source_episode_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result_id,
            workspace_id,
            experiment_id,
            theory_id,
            kind.value,
            summary,
            json.dumps(metrics, sort_keys=True),
            artifact_path,
            confidence,
            observed_at,
            source_episode_id,
            created_at,
        ),
    )


def get_experiment_result(conn: sqlite3.Connection, result_id: str) -> ExperimentResult | None:
    row = conn.execute("SELECT * FROM experiment_results WHERE id = ?", (result_id,)).fetchone()
    return row_to_result(row) if row is not None else None


def list_experiment_results(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    experiment_id: str | None = None,
    theory_id: str | None = None,
    limit: int = 20,
) -> list[ExperimentResult]:
    clauses = ["workspace_id = ?"]
    params: list[str | int] = [workspace_id]
    if experiment_id is not None:
        clauses.append("experiment_id = ?")
        params.append(experiment_id)
    if theory_id is not None:
        clauses.append("theory_id = ?")
        params.append(theory_id)
    rows = conn.execute(
        f"""
        SELECT * FROM experiment_results
        WHERE {" AND ".join(clauses)}
        ORDER BY observed_at DESC, created_at DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [row_to_result(row) for row in rows]
