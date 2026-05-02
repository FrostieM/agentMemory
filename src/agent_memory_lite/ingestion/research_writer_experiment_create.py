"""Create a new research experiment."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import NotFoundError
from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.ingestion.research_writer_shared import validate_workspace
from agent_memory_lite.models.research import Experiment, ExperimentIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.research_repo import (
    get_experiment,
    get_snapshot,
    insert_experiment_row,
)
from agent_memory_lite.repositories.theories_repo import get_theory
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_experiment(conn: sqlite3.Connection, payload: ExperimentIn) -> Experiment:
    if payload.theory_id is not None:
        theory = get_theory(conn, payload.theory_id)
        if theory is None:
            raise NotFoundError(f"theory_id {payload.theory_id!r} not found")
        validate_workspace(
            item_workspace_id=theory.workspace_id,
            payload_workspace_id=payload.workspace_id,
            field_name="theory_id",
        )
    if payload.snapshot_id is not None:
        snapshot = get_snapshot(conn, payload.snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"snapshot_id {payload.snapshot_id!r} not found")
        validate_workspace(
            item_workspace_id=snapshot.workspace_id,
            payload_workspace_id=payload.workspace_id,
            field_name="snapshot_id",
        )

    experiment_id = new_id(IdKind.EXPERIMENT)
    timestamp = iso_now()
    with with_tx(conn):
        insert_experiment_row(
            conn,
            experiment_id=experiment_id,
            workspace_id=payload.workspace_id,
            theory_id=payload.theory_id,
            snapshot_id=payload.snapshot_id,
            title=payload.title,
            hypothesis=payload.hypothesis,
            cohort_definition=payload.cohort_definition,
            success_criteria=payload.success_criteria,
            command=payload.command,
            status=payload.status,
            priority=payload.priority,
            owner=payload.owner,
            due_at=payload.due_at,
            source_episode_id=payload.source_episode_id,
            metadata=payload.metadata,
            created_at=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="write_experiment",
            target_type="research_experiment",
            target_id=experiment_id,
            source_episode_id=payload.source_episode_id,
            after={
                "title": payload.title,
                "theory_id": payload.theory_id,
                "snapshot_id": payload.snapshot_id,
                "status": payload.status.value,
            },
        )
    experiment = get_experiment(conn, experiment_id)
    assert experiment is not None
    return experiment
