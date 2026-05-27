"""Find important objects without capability_links influence."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.hygiene_capabilities import (
    load_active_capabilities,
    suggest_capability_links,
)
from agent_memory_lite.maintenance.hygiene_models import HygieneFinding, table_exists

# Per-target SQL columns + filter. Spec parts:
#   (target_type, table, columns_sql, where_sql_after_AND, score_field)
_SPECS = [
    (
        "theory",
        "theories",
        """
        id,
        title AS label,
        importance,
        status,
        COALESCE(title, '') || ' ' ||
        COALESCE(domain, '') || ' ' ||
        COALESCE(claim, '') || ' ' ||
        COALESCE(mechanism, '') || ' ' ||
        COALESCE(experiment_plan, '') || ' ' ||
        COALESCE(predictions_json, '') || ' ' ||
        COALESCE(validation_criteria_json, '') || ' ' ||
        COALESCE(tags_json, '') AS search_text
        """,
        "importance >= ? AND status IN ('proposed', 'testing', 'supported', 'validated', 'rejected')",
        "importance",
    ),
    (
        "experiment",
        "experiments",
        """
        id,
        title AS label,
        priority AS importance,
        status,
        COALESCE(title, '') || ' ' ||
        COALESCE(hypothesis, '') || ' ' ||
        COALESCE(cohort_definition, '') || ' ' ||
        COALESCE(success_criteria_json, '') || ' ' ||
        COALESCE(command, '') || ' ' ||
        COALESCE(owner, '') || ' ' ||
        COALESCE(metadata_json, '') AS search_text
        """,
        "priority >= ? AND status IN ('planned', 'running', 'blocked')",
        "priority",
    ),
    (
        "decision",
        "decisions",
        """
        id,
        title AS label,
        importance,
        status,
        COALESCE(title, '') || ' ' ||
        COALESCE(decision_text, '') || ' ' ||
        COALESCE(rationale, '') AS search_text
        """,
        "importance >= ? AND status = 'active'",
        "importance",
    ),
]


def find_unlinked_capability_targets(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    importance_threshold: float,
) -> list[HygieneFinding]:
    if not table_exists(conn, "capability_links"):
        return []
    capabilities = load_active_capabilities(conn, workspace_id=workspace_id)
    findings: list[HygieneFinding] = []
    for target_type, table, columns, where_sql, score_field in _SPECS:
        if not table_exists(conn, table):
            continue
        rows = conn.execute(
            f"""
            SELECT {columns}
            FROM {table} t
            WHERE t.workspace_id = ?
              AND {where_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM capability_links l
                WHERE l.workspace_id = t.workspace_id
                  AND l.target_type = ?
                  AND l.target_id = t.id
              )
            ORDER BY {score_field} DESC
            """,
            (workspace_id, importance_threshold, target_type),
        ).fetchall()
        for row in rows:
            target_id = str(row["id"])
            label = str(row["label"])
            search_text = str(row["search_text"])
            findings.append(
                HygieneFinding(
                    kind="missing_capability_link",
                    severity="warning",
                    target_type=target_type,
                    target_id=target_id,
                    summary="Important object has no role/skill/playbook influence link.",
                    details={
                        "label": label,
                        "status": row["status"],
                        "importance": row["importance"],
                        "suggested_capability_links": suggest_capability_links(
                            capabilities,
                            workspace_id=workspace_id,
                            target_type=target_type,
                            target_id=target_id,
                            target_label=label,
                            target_text=search_text,
                        ),
                    },
                )
            )
    return findings
