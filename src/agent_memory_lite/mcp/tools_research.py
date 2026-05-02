"""Research-lab MCP tool handlers (write side)."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.ingestion.research_writer import (
    add_experiment_result,
    distill_insight,
    register_snapshot,
    update_insight,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.models.research import (
    DomainConceptIn,
    ExperimentIn,
    ExperimentResultIn,
    MemorySnapshotIn,
    ResearchInsightIn,
    ResearchInsightUpdateIn,
)


def memory_register_snapshot(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    snapshot = register_snapshot(conn, MemorySnapshotIn(**payload))
    return {
        "snapshot_id": snapshot.id,
        "snapshot_key": snapshot.snapshot_key,
        "total_rows": snapshot.total_rows,
        "duckdb_path": snapshot.duckdb_path,
        "updated_at": snapshot.updated_at,
    }


def memory_write_experiment(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    experiment = write_experiment(conn, ExperimentIn(**payload))
    return {
        "experiment_id": experiment.id,
        "theory_id": experiment.theory_id,
        "snapshot_id": experiment.snapshot_id,
        "status": experiment.status.value,
        "priority": experiment.priority,
    }


def memory_add_experiment_result(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    result = add_experiment_result(conn, ExperimentResultIn(**payload))
    return {
        "result_id": result.id,
        "experiment_id": result.experiment_id,
        "theory_id": result.theory_id,
        "kind": result.kind.value,
        "observed_at": result.observed_at,
    }


def memory_upsert_concept(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    concept = upsert_domain_concept(conn, DomainConceptIn(**payload))
    return {
        "concept_id": concept.id,
        "name": concept.name,
        "kind": concept.kind.value,
        "confidence": concept.confidence,
        "active": concept.active,
    }


def memory_distill_insight(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    insight = distill_insight(conn, ResearchInsightIn(**payload))
    return {
        "insight_id": insight.id,
        "insight_type": insight.insight_type.value,
        "status": insight.status.value,
        "target_type": insight.target_type,
        "target_id": insight.target_id,
    }


def memory_update_insight(
    *, conn: sqlite3.Connection, payload: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    insight = update_insight(conn, ResearchInsightUpdateIn(**payload))
    return {
        "insight_id": insight.id,
        "insight_type": insight.insight_type.value,
        "status": insight.status.value,
        "target_type": insight.target_type,
        "target_id": insight.target_id,
        "updated_at": insight.updated_at,
    }
