"""SQL operations for canonical ``experiments``."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import CapabilityLinkTargetType, ExperimentStatus
from agent_memory_lite.models.research import Experiment
from agent_memory_lite.repositories.capability_links_repo import capability_link_text_by_target
from agent_memory_lite.repositories.research_helpers import (
    contains_all,
    row_to_experiment,
    tokens_from,
)
from agent_memory_lite.utils.sql_filters import date_range_clause


def insert_experiment_row(
    conn: sqlite3.Connection,
    *,
    experiment_id: str,
    workspace_id: str,
    theory_id: str | None,
    snapshot_id: str | None,
    title: str,
    hypothesis: str,
    cohort_definition: str | None,
    success_criteria: dict[str, Any],
    command: str | None,
    status: ExperimentStatus,
    priority: float,
    owner: str | None,
    due_at: str | None,
    source_episode_id: str | None,
    metadata: dict[str, Any],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO experiments (
            id, workspace_id, theory_id, snapshot_id, title, hypothesis,
            cohort_definition, success_criteria_json, command, status, priority,
            owner, due_at, source_episode_id, metadata_json, created_at,
            updated_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            experiment_id,
            workspace_id,
            theory_id,
            snapshot_id,
            title,
            hypothesis,
            cohort_definition,
            json.dumps(success_criteria, sort_keys=True),
            command,
            status.value,
            priority,
            owner,
            due_at,
            source_episode_id,
            json.dumps(metadata, sort_keys=True),
            created_at,
            created_at,
        ),
    )


def get_experiment(conn: sqlite3.Connection, experiment_id: str) -> Experiment | None:
    row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    return row_to_experiment(row) if row is not None else None


def _experiment_text(experiment: Experiment, linked_text: str = "") -> str:
    return " ".join(
        [
            experiment.title,
            experiment.hypothesis,
            experiment.cohort_definition or "",
            experiment.command or "",
            " ".join(str(key) for key in experiment.success_criteria),
            linked_text,
        ]
    )


def _rank_experiment(
    experiment: Experiment, tokens: list[str], linked_text: str = ""
) -> tuple[float, str]:
    status_bonus = {
        ExperimentStatus.RUNNING: 0.35,
        ExperimentStatus.PLANNED: 0.25,
        ExperimentStatus.BLOCKED: 0.05,
        ExperimentStatus.COMPLETED: -0.20,
        ExperimentStatus.CANCELLED: -0.35,
    }[experiment.status]
    text = _experiment_text(experiment, linked_text).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    return token_score + experiment.priority + status_bonus, experiment.updated_at


def list_experiments(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    statuses: list[ExperimentStatus] | None = None,
    limit: int = 20,
    since: str | None = None,
    until: str | None = None,
) -> list[Experiment]:
    extra_sql, extra_params = date_range_clause(since=since, until=until)
    rows = conn.execute(
        f"SELECT * FROM experiments WHERE workspace_id = ? {extra_sql}",
        (workspace_id, *extra_params),
    ).fetchall()
    experiments = [row_to_experiment(row) for row in rows]
    if statuses is not None:
        allowed = {status.value for status in statuses}
        experiments = [item for item in experiments if item.status.value in allowed]
    terms = tokens_from(query)
    linked_text = capability_link_text_by_target(
        conn,
        workspace_id=workspace_id,
        target_type=CapabilityLinkTargetType.EXPERIMENT,
        target_ids=[item.id for item in experiments],
    )
    experiments = [
        item
        for item in experiments
        if contains_all(_experiment_text(item, linked_text.get(item.id, "")), terms)
    ]
    experiments.sort(
        key=lambda item: _rank_experiment(item, terms, linked_text.get(item.id, "")),
        reverse=True,
    )
    return experiments[:limit]


def mark_experiment_completed(
    conn: sqlite3.Connection,
    *,
    experiment_id: str,
    updated_at: str,
    completed_at: str,
) -> None:
    conn.execute(
        """
        UPDATE experiments
        SET status = 'completed', updated_at = ?, completed_at = COALESCE(completed_at, ?)
        WHERE id = ?
        """,
        (updated_at, completed_at, experiment_id),
    )
