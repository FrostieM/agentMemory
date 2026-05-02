"""Research-lab MCP tool handlers (list side)."""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.repositories.research_repo import (
    build_research_agenda,
    list_concepts,
    list_insights,
)


def memory_list_research_agenda(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    limit: int = 10,
    **_kwargs: Any,
) -> dict[str, Any]:
    agenda = build_research_agenda(conn, workspace_id=workspace_id, query=query, limit=limit)
    return {
        "snapshots": [
            {
                "snapshot_id": item.id,
                "snapshot_key": item.snapshot_key,
                "title": item.title,
                "total_rows": item.total_rows,
                "duckdb_path": item.duckdb_path,
            }
            for item in agenda.snapshots
        ],
        "experiments": [
            {
                "experiment_id": item.id,
                "title": item.title,
                "theory_id": item.theory_id,
                "snapshot_id": item.snapshot_id,
                "status": item.status.value,
                "priority": item.priority,
                "hypothesis": item.hypothesis,
            }
            for item in agenda.experiments
        ],
        "insights": [
            {
                "insight_id": item.id,
                "insight_type": item.insight_type.value,
                "summary": item.summary,
                "status": item.status.value,
                "confidence": item.confidence,
                "target_type": item.target_type,
                "target_id": item.target_id,
            }
            for item in agenda.insights
        ],
        "concepts": [
            {
                "concept_id": item.id,
                "name": item.name,
                "kind": item.kind.value,
                "definition": item.definition,
                "confidence": item.confidence,
            }
            for item in agenda.concepts
        ],
    }


def memory_list_concepts(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    concepts = list_concepts(
        conn,
        workspace_id=workspace_id,
        query=query,
        include_inactive=include_inactive,
        limit=limit,
    )
    return {
        "concepts": [
            {
                "concept_id": item.id,
                "name": item.name,
                "kind": item.kind.value,
                "definition": item.definition,
                "confidence": item.confidence,
                "active": item.active,
            }
            for item in concepts
        ],
    }


def memory_list_insights(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    query: str | None = None,
    limit: int = 20,
    **_kwargs: Any,
) -> dict[str, Any]:
    insights = list_insights(conn, workspace_id=workspace_id, query=query, limit=limit)
    return {
        "insights": [
            {
                "insight_id": item.id,
                "insight_type": item.insight_type.value,
                "summary": item.summary,
                "status": item.status.value,
                "confidence": item.confidence,
                "target_type": item.target_type,
                "target_id": item.target_id,
            }
            for item in insights
        ],
    }
