"""SQL operations for ``research_insights``."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.enums import (
    CapabilityLinkTargetType,
    InsightStatus,
    InsightType,
)
from agent_memory_lite.models.research import ResearchInsight
from agent_memory_lite.repositories.capability_links_repo import capability_link_text_by_target
from agent_memory_lite.repositories.research_helpers import (
    contains_all,
    row_to_insight,
    tokens_from,
)
from agent_memory_lite.utils.sql_filters import date_range_clause


def insert_insight_row(
    conn: sqlite3.Connection,
    *,
    insight_id: str,
    workspace_id: str,
    insight_type: InsightType,
    summary: str,
    proposed_action: str | None,
    target_type: str | None,
    target_id: str | None,
    source_episode_ids: list[str],
    confidence: float,
    status: InsightStatus,
    tags: list[str],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO research_insights (
            id, workspace_id, insight_type, summary, proposed_action,
            target_type, target_id, source_episode_ids_json, confidence,
            status, tags_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            insight_id,
            workspace_id,
            insight_type.value,
            summary,
            proposed_action,
            target_type,
            target_id,
            json.dumps(source_episode_ids, sort_keys=True),
            confidence,
            status.value,
            json.dumps(tags, sort_keys=True),
            created_at,
            created_at,
        ),
    )


def get_insight(conn: sqlite3.Connection, insight_id: str) -> ResearchInsight | None:
    row = conn.execute("SELECT * FROM research_insights WHERE id = ?", (insight_id,)).fetchone()
    return row_to_insight(row) if row is not None else None


def update_insight_row(
    conn: sqlite3.Connection,
    *,
    insight_id: str,
    target_type: str | None = None,
    target_id: str | None = None,
    status: InsightStatus | None = None,
    updated_at: str,
) -> None:
    assignments: list[str] = []
    values: list[str] = []
    if target_type is not None and target_id is not None:
        assignments.extend(["target_type = ?", "target_id = ?"])
        values.extend([target_type, target_id])
    if status is not None:
        assignments.append("status = ?")
        values.append(status.value)
    if not assignments:
        return
    assignments.append("updated_at = ?")
    values.append(updated_at)
    values.append(insight_id)
    conn.execute(
        f"UPDATE research_insights SET {', '.join(assignments)} WHERE id = ?",
        tuple(values),
    )


def _insight_text(insight: ResearchInsight, linked_text: str = "") -> str:
    return " ".join(
        [
            insight.summary,
            insight.proposed_action or "",
            insight.target_type or "",
            insight.target_id or "",
            " ".join(insight.tags),
            linked_text,
        ]
    )


def _rank_insight(
    insight: ResearchInsight, tokens: list[str], linked_text: str = ""
) -> tuple[float, str]:
    status_bonus = {
        InsightStatus.NEW: 0.30,
        InsightStatus.ACCEPTED: 0.18,
        InsightStatus.REJECTED: -0.25,
        InsightStatus.ARCHIVED: -0.35,
    }[insight.status]
    text = _insight_text(insight, linked_text).lower()
    token_score = sum(1.0 for token in tokens if token in text)
    return token_score + insight.confidence + status_bonus, insight.updated_at


def list_insights(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    query: str | None = None,
    statuses: list[InsightStatus] | None = None,
    limit: int = 20,
    since: str | None = None,
    until: str | None = None,
) -> list[ResearchInsight]:
    extra_sql, extra_params = date_range_clause(since=since, until=until)
    rows = conn.execute(
        f"SELECT * FROM research_insights WHERE workspace_id = ? {extra_sql}",
        (workspace_id, *extra_params),
    ).fetchall()
    insights = [row_to_insight(row) for row in rows]
    if statuses is not None:
        allowed = {status.value for status in statuses}
        insights = [item for item in insights if item.status.value in allowed]
    terms = tokens_from(query)
    linked_text = capability_link_text_by_target(
        conn,
        workspace_id=workspace_id,
        target_type=CapabilityLinkTargetType.RESEARCH_INSIGHT,
        target_ids=[item.id for item in insights],
    )
    insights = [
        item
        for item in insights
        if contains_all(_insight_text(item, linked_text.get(item.id, "")), terms)
    ]
    insights.sort(
        key=lambda item: _rank_insight(item, terms, linked_text.get(item.id, "")),
        reverse=True,
    )
    return insights[:limit]
