"""Archive → restore should land on the prior status, not a default.

When a theory at ``status='supported'`` gets archived and later
restored, it should come back as ``supported`` — not a generic
``proposed``. Same for an insight at ``status='accepted'`` (restore
should land back on ``accepted`` rather than the default ``new``).
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.ingestion.archive_service import archive_memory_object


def _seed_theory(
    conn: sqlite3.Connection, *, theory_id: str, workspace_id: str, status: str
) -> None:
    conn.execute(
        """
        INSERT INTO theories (
            id, workspace_id, title, domain, claim, predictions_json,
            validation_criteria_json, dependent_decision_ids_json, tags_json,
            status, source_episode_id, confidence, importance, evidence_count,
            evidence_strength, created_at, updated_at, last_tested_at,
            mechanism, experiment_plan, supersedes_theory_id
        ) VALUES (
            ?, ?, 'T', 'd', 'c', '[]', '[]', '[]', '[]',
            ?, NULL, 0.5, 0.5, 0, 0.0, '2026-01-01', '2026-01-01', NULL,
            NULL, NULL, NULL
        )
        """,
        (theory_id, workspace_id, status),
    )
    conn.commit()


def _seed_insight(
    conn: sqlite3.Connection, *, insight_id: str, workspace_id: str, status: str
) -> None:
    conn.execute(
        """
        INSERT INTO research_insights (
            id, workspace_id, insight_type, summary, proposed_action,
            target_type, target_id, source_episode_ids_json, confidence,
            status, tags_json, created_at, updated_at
        ) VALUES (?, ?, 'open_question', 'S', NULL, NULL, NULL, '[]', 0.5, ?, '[]', '2026-01-01', '2026-01-01')
        """,
        (insight_id, workspace_id, status),
    )
    conn.commit()


def _row_status(conn: sqlite3.Connection, *, table: str, row_id: str) -> str:
    row = conn.execute(f"SELECT status FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return row["status"]


@pytest.mark.parametrize(
    ("kind", "table", "prior"),
    [
        ("theory", "theories", "supported"),
        ("theory", "theories", "validated"),
        ("insight", "research_insights", "accepted"),
    ],
)
def test_restore_returns_to_prior_status(
    applied_conn: sqlite3.Connection, kind: str, table: str, prior: str
) -> None:
    workspace = "archive-prior-ws"
    object_id = f"{kind}_test"
    if kind == "theory":
        _seed_theory(applied_conn, theory_id=object_id, workspace_id=workspace, status=prior)
    else:
        _seed_insight(applied_conn, insight_id=object_id, workspace_id=workspace, status=prior)

    archive_memory_object(
        applied_conn,
        workspace_id=workspace,
        kind=kind,
        object_id=object_id,
        archive=True,
    )
    assert _row_status(applied_conn, table=table, row_id=object_id) == "archived"

    archive_memory_object(
        applied_conn,
        workspace_id=workspace,
        kind=kind,
        object_id=object_id,
        archive=False,
    )
    assert _row_status(applied_conn, table=table, row_id=object_id) == prior


def test_restore_falls_back_to_default_when_no_audit_present(
    applied_conn: sqlite3.Connection,
) -> None:
    """A row pre-existing the prior-status fix has no archive audit
    entry. Restore should still succeed using the spec default
    rather than crashing."""
    workspace = "legacy-archive-ws"
    object_id = "theory_legacy"
    _seed_theory(applied_conn, theory_id=object_id, workspace_id=workspace, status="archived")
    # No insert_audit was ever called for this row, so the audit log
    # is empty for the archive action.
    archive_memory_object(
        applied_conn,
        workspace_id=workspace,
        kind="theory",
        object_id=object_id,
        archive=False,
    )
    assert _row_status(applied_conn, table="theories", row_id=object_id) == "proposed"
