"""Shared search helpers + row converters for research-lab repos."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import (
    ConceptKind,
    ExperimentStatus,
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
)
from agent_memory_lite.models.research import (
    DomainConcept,
    Experiment,
    ExperimentResult,
    MemorySnapshot,
    ResearchInsight,
)

_TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


def json_list(raw: str | None) -> list[str]:
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def json_dict(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def json_int_dict(raw: str | None) -> dict[str, int]:
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in data.items():
        if isinstance(value, bool | int | float):
            result[str(key)] = int(value)
    return result


def tokens_from(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in _TOKEN_RE.findall(query) if len(token) > 1]


def contains_all(text: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    lower = text.lower()
    return any(token in lower for token in tokens)


def row_to_snapshot(row: sqlite3.Row) -> MemorySnapshot:
    return MemorySnapshot(
        id=row["id"],
        workspace_id=row["workspace_id"],
        snapshot_key=row["snapshot_key"],
        title=row["title"],
        source=row["source_label"],
        db_path=row["db_path"],
        duckdb_path=row["duckdb_path"],
        parquet_dir=row["parquet_dir"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        build_sha=row["build_sha"],
        build_branch=row["build_branch"],
        build_time=row["build_time"],
        remote_host=row["remote_host"],
        table_counts=json_int_dict(row["table_counts_json"]),
        total_rows=int(row["total_rows"]),
        metadata=json_dict(row["metadata_json"]),
        source_episode_id=row["source_episode_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_experiment(row: sqlite3.Row) -> Experiment:
    return Experiment(
        id=row["id"],
        workspace_id=row["workspace_id"],
        theory_id=row["theory_id"],
        snapshot_id=row["snapshot_id"],
        title=row["title"],
        hypothesis=row["hypothesis"],
        cohort_definition=row["cohort_definition"],
        success_criteria=json_dict(row["success_criteria_json"]),
        command=row["command"],
        status=ExperimentStatus(row["status"]),
        priority=float(row["priority"]),
        owner=row["owner"],
        due_at=row["due_at"],
        source_episode_id=row["source_episode_id"],
        metadata=json_dict(row["metadata_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def row_to_result(row: sqlite3.Row) -> ExperimentResult:
    return ExperimentResult(
        id=row["id"],
        workspace_id=row["workspace_id"],
        experiment_id=row["experiment_id"],
        theory_id=row["theory_id"],
        kind=TheoryEvidenceKind(row["kind"]),
        summary=row["summary"],
        metrics=json_dict(row["metrics_json"]),
        artifact_path=row["artifact_path"],
        confidence=float(row["confidence"]),
        observed_at=row["observed_at"],
        source_episode_id=row["source_episode_id"],
        created_at=row["created_at"],
    )


def row_to_concept(row: sqlite3.Row) -> DomainConcept:
    return DomainConcept(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        kind=ConceptKind(row["kind"]),
        definition=row["definition"],
        aliases=json_list(row["aliases_json"]),
        tags=json_list(row["tags_json"]),
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_insight(row: sqlite3.Row) -> ResearchInsight:
    return ResearchInsight(
        id=row["id"],
        workspace_id=row["workspace_id"],
        insight_type=InsightType(row["insight_type"]),
        summary=row["summary"],
        proposed_action=row["proposed_action"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        source_episode_ids=json_list(row["source_episode_ids_json"]),
        confidence=float(row["confidence"]),
        status=InsightStatus(row["status"]),
        tags=json_list(row["tags_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
