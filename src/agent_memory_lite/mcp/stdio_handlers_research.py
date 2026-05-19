"""Handlers for the research-lab write surface (snapshots + experiments
+ concepts + insights)."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion.research_writer import (
    add_experiment_result,
    distill_insight,
    register_snapshot,
    update_insight,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_write
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.models.research import (
    DomainConceptIn,
    ExperimentIn,
    ExperimentResultIn,
    MemorySnapshotIn,
    ResearchInsightIn,
    ResearchInsightUpdateIn,
)


def _handle_register_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/register_snapshot", payload, log_label="register_snapshot")
    if delegated is not None:
        return delegated

    snapshot = register_snapshot(
        _runtime.db_for(str(payload["workspace_id"])), MemorySnapshotIn(**payload)
    )
    return {
        "snapshot_id": snapshot.id,
        "snapshot_key": snapshot.snapshot_key,
        "total_rows": snapshot.total_rows,
        "duckdb_path": snapshot.duckdb_path,
        "updated_at": snapshot.updated_at,
    }


def _handle_write_experiment(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/write_experiment", payload, log_label="write_experiment")
    if delegated is not None:
        return delegated

    experiment = write_experiment(
        _runtime.db_for(str(payload["workspace_id"])), ExperimentIn(**payload)
    )
    return {
        "experiment_id": experiment.id,
        "theory_id": experiment.theory_id,
        "snapshot_id": experiment.snapshot_id,
        "status": experiment.status.value,
        "priority": experiment.priority,
    }


def _handle_add_experiment_result(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write(
        "/memory/add_experiment_result",
        payload,
        log_label="add_experiment_result",
    )
    if delegated is not None:
        return delegated

    result = add_experiment_result(
        _runtime.db_for(str(payload["workspace_id"])), ExperimentResultIn(**payload)
    )
    return {
        "result_id": result.id,
        "experiment_id": result.experiment_id,
        "theory_id": result.theory_id,
        "kind": result.kind.value,
        "observed_at": result.observed_at,
    }


def _handle_upsert_concept(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/upsert_concept", payload, log_label="upsert_concept")
    if delegated is not None:
        return delegated

    concept = upsert_domain_concept(
        _runtime.db_for(str(payload["workspace_id"])), DomainConceptIn(**payload)
    )
    return {
        "concept_id": concept.id,
        "name": concept.name,
        "kind": concept.kind.value,
        "confidence": concept.confidence,
        "active": concept.active,
    }


def _handle_distill_insight(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/distill_insight", payload, log_label="distill_insight")
    if delegated is not None:
        return delegated

    insight = distill_insight(
        _runtime.db_for(str(payload["workspace_id"])), ResearchInsightIn(**payload)
    )
    return {
        "insight_id": insight.id,
        "insight_type": insight.insight_type.value,
        "status": insight.status.value,
        "target_type": insight.target_type,
        "target_id": insight.target_id,
    }


def _handle_update_insight(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/update_insight", payload, log_label="update_insight")
    if delegated is not None:
        return delegated

    insight = update_insight(
        _runtime.db_for(str(payload["workspace_id"])), ResearchInsightUpdateIn(**payload)
    )
    return {
        "insight_id": insight.id,
        "insight_type": insight.insight_type.value,
        "status": insight.status.value,
        "target_type": insight.target_type,
        "target_id": insight.target_id,
        "updated_at": insight.updated_at,
    }
