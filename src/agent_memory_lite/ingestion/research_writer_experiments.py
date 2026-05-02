"""Record experiment results.

Experiment creation lives in ``research_writer_experiment_create.py``;
theory-side updates triggered by a result live in
``research_writer_theory_update.py``. The ``write_experiment`` symbol
is re-exported here so import paths in ``research_writer.py`` stay
unchanged.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.api.errors import NotFoundError, ValidationError
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.ingestion.research_writer_experiment_create import write_experiment
from agent_memory_lite.ingestion.research_writer_shared import validate_workspace
from agent_memory_lite.ingestion.research_writer_theory_update import (
    update_theory_after_result,
)
from agent_memory_lite.models.research import (
    Experiment,
    ExperimentResult,
    ExperimentResultIn,
)
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.research_repo import (
    get_experiment,
    get_experiment_result,
    insert_experiment_result_row,
    mark_experiment_completed,
)
from agent_memory_lite.repositories.theories_repo import get_theory
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

__all__ = ["add_experiment_result", "write_experiment"]


def _result_metrics_with_links(
    metrics: dict[str, Any], *, experiment_id: str, result_id: str
) -> dict[str, Any]:
    linked = dict(metrics)
    linked["experiment_id"] = experiment_id
    linked["experiment_result_id"] = result_id
    return linked


def _resolve_result_theory(
    conn: sqlite3.Connection,
    *,
    payload: ExperimentResultIn,
    experiment: Experiment,
) -> str | None:
    if (
        payload.theory_id is not None
        and experiment.theory_id is not None
        and payload.theory_id != experiment.theory_id
    ):
        raise ValidationError("theory_id must match the experiment theory_id")
    theory_id = payload.theory_id or experiment.theory_id
    if theory_id is None:
        return None
    theory = get_theory(conn, theory_id)
    if theory is None:
        raise NotFoundError(f"theory_id {theory_id!r} not found")
    validate_workspace(
        item_workspace_id=theory.workspace_id,
        payload_workspace_id=payload.workspace_id,
        field_name="theory_id",
    )
    return theory_id


def add_experiment_result(
    conn: sqlite3.Connection, payload: ExperimentResultIn
) -> ExperimentResult:
    experiment = get_experiment(conn, payload.experiment_id)
    if experiment is None:
        raise NotFoundError(f"experiment_id {payload.experiment_id!r} not found")
    validate_workspace(
        item_workspace_id=experiment.workspace_id,
        payload_workspace_id=payload.workspace_id,
        field_name="experiment_id",
    )
    theory_id = _resolve_result_theory(conn, payload=payload, experiment=experiment)

    result_id = new_id(IdKind.EXPERIMENT_RESULT)
    timestamp = iso_now()
    observed_at = payload.observed_at or timestamp
    metrics = _result_metrics_with_links(
        payload.metrics, experiment_id=payload.experiment_id, result_id=result_id
    )

    with with_tx(conn):
        insert_experiment_result_row(
            conn,
            result_id=result_id,
            workspace_id=payload.workspace_id,
            experiment_id=payload.experiment_id,
            theory_id=theory_id,
            kind=payload.kind,
            summary=payload.summary,
            metrics=metrics,
            artifact_path=payload.artifact_path,
            confidence=payload.confidence,
            observed_at=observed_at,
            source_episode_id=payload.source_episode_id,
            created_at=timestamp,
        )
        mark_experiment_completed(
            conn,
            experiment_id=payload.experiment_id,
            updated_at=timestamp,
            completed_at=observed_at,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="add_experiment_result",
            target_type="experiment_result",
            target_id=result_id,
            source_episode_id=payload.source_episode_id,
            after={
                "experiment_id": payload.experiment_id,
                "theory_id": theory_id,
                "kind": payload.kind.value,
                "artifact_path": payload.artifact_path,
            },
        )
        if theory_id is not None:
            update_theory_after_result(
                conn,
                theory_id=theory_id,
                payload=payload,
                timestamp=timestamp,
                observed_at=observed_at,
                metrics=metrics,
            )

    result = get_experiment_result(conn, result_id)
    assert result is not None
    return result
