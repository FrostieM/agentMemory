"""Handlers for the research-lab read surface (research_agenda + concepts + insights)."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _workspace_from_args
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.repositories.research_repo import (
    build_research_agenda,
    list_concepts,
    list_insights,
)


def _handle_list_research_agenda(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    agenda = build_research_agenda(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        query=args.get("query"),
        limit=int(args.get("limit", 10)),
        since=args.get("since"),
        until=args.get("until"),
    )
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


def _handle_list_concepts(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    concepts = list_concepts(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        query=args.get("query"),
        include_inactive=bool(args.get("include_inactive", False)),
        limit=int(args.get("limit", 20)),
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


def _handle_list_insights(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    insights = list_insights(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        query=args.get("query"),
        limit=int(args.get("limit", 20)),
    )
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
