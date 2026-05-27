"""Aggregator facade for research-lab repos.

Per-table SQL lives in:
- ``research_snapshots_repo.py``
- ``experiments_repo.py``
- ``research_results_repo.py``
- ``research_concepts_repo.py``
- ``insights_repo.py``

Shared search helpers + row converters live in
``research_helpers.py``. This module re-exports the public API and
provides ``build_research_agenda`` which fetches all four kinds at
once for the retrieval pipeline.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import ExperimentStatus, InsightStatus
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.repositories.research_concepts_repo import (
    get_concept_by_id,
    get_concept_by_name,
    list_concepts,
    upsert_concept_row,
)
from agent_memory_lite.repositories.research_experiments_repo import (
    get_experiment,
    insert_experiment_row,
    list_experiments,
    mark_experiment_completed,
)
from agent_memory_lite.repositories.research_insights_repo import (
    get_insight,
    insert_insight_row,
    list_insights,
    update_insight_row,
)
from agent_memory_lite.repositories.research_results_repo import (
    get_experiment_result,
    insert_experiment_result_row,
    list_experiment_results,
)
from agent_memory_lite.repositories.research_snapshots_repo import (
    get_snapshot,
    get_snapshot_by_key,
    list_snapshots,
    upsert_snapshot_row,
)

__all__ = [
    "build_research_agenda",
    "get_concept_by_id",
    "get_concept_by_name",
    "get_experiment",
    "get_experiment_result",
    "get_insight",
    "get_snapshot",
    "get_snapshot_by_key",
    "insert_experiment_result_row",
    "insert_experiment_row",
    "insert_insight_row",
    "list_concepts",
    "list_experiment_results",
    "list_experiments",
    "list_insights",
    "list_snapshots",
    "mark_experiment_completed",
    "update_insight_row",
    "upsert_concept_row",
    "upsert_snapshot_row",
]


def build_research_agenda(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    limit: int = 10,
    since: str | None = None,
    until: str | None = None,
) -> ResearchAgenda:
    return ResearchAgenda(
        snapshots=list_snapshots(
            conn,
            workspace_id=workspace_id,
            query=query,
            limit=max(1, limit // 3),
            since=since,
            until=until,
        ),
        experiments=list_experiments(
            conn,
            workspace_id=workspace_id,
            query=query,
            statuses=[ExperimentStatus.PLANNED, ExperimentStatus.RUNNING, ExperimentStatus.BLOCKED],
            limit=limit,
            since=since,
            until=until,
        ),
        insights=list_insights(
            conn,
            workspace_id=workspace_id,
            query=query,
            statuses=[InsightStatus.NEW, InsightStatus.ACCEPTED],
            limit=limit,
            since=since,
            until=until,
        ),
        concepts=list_concepts(
            conn,
            workspace_id=workspace_id,
            query=query,
            limit=max(1, limit // 2),
            since=since,
            until=until,
        ),
    )
