"""SQL operations for research theories.

Search + ranking + ``list_theories`` live in ``theories_search.py``;
evidence INSERT / GET / LIST live in ``theory_evidence_repo.py``.
This module owns INSERT / archive / status-update / by-id lookups.
The evidence helpers are re-exported so existing import paths keep
working.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.enums import TheoryStatus
from agent_memory_lite.models.theories import Theory
from agent_memory_lite.repositories.theories_search import list_theories, row_to_theory
from agent_memory_lite.repositories.theory_evidence_repo import (
    get_theory_evidence,
    insert_theory_evidence_row,
    list_evidence_for_theory,
)

__all__ = [
    "archive_theory",
    "get_theory",
    "get_theory_evidence",
    "insert_theory_evidence_row",
    "insert_theory_row",
    "list_evidence_for_theory",
    "list_theories",
    "update_theory_confidence_status",
]


def insert_theory_row(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    workspace_id: str,
    title: str,
    domain: str,
    claim: str,
    mechanism: str | None,
    predictions: list[str],
    validation_criteria: list[str],
    experiment_plan: str | None,
    dependent_decision_ids: list[str],
    tags: list[str],
    status: TheoryStatus,
    supersedes_theory_id: str | None,
    source_episode_id: str | None,
    confidence: float,
    importance: float,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO theories (
            id, workspace_id, title, domain, claim, mechanism,
            predictions_json, validation_criteria_json, experiment_plan,
            dependent_decision_ids_json, tags_json, status,
            supersedes_theory_id, source_episode_id, confidence, importance,
            evidence_count, evidence_strength, created_at, updated_at, last_tested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0.0, ?, ?, NULL)
        """,
        (
            theory_id,
            workspace_id,
            title,
            domain,
            claim,
            mechanism,
            json.dumps(predictions, sort_keys=True),
            json.dumps(validation_criteria, sort_keys=True),
            experiment_plan,
            json.dumps(dependent_decision_ids, sort_keys=True),
            json.dumps(tags, sort_keys=True),
            status.value,
            supersedes_theory_id,
            source_episode_id,
            confidence,
            importance,
            created_at,
            created_at,
        ),
    )


def archive_theory(conn: sqlite3.Connection, *, theory_id: str, updated_at: str) -> None:
    conn.execute(
        "UPDATE theories SET status = 'superseded', updated_at = ? WHERE id = ?",
        (updated_at, theory_id),
    )


def update_theory_confidence_status(
    conn: sqlite3.Connection,
    *,
    theory_id: str,
    confidence: float,
    status: TheoryStatus,
    updated_at: str,
    last_tested_at: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE theories
        SET confidence = ?,
            status = ?,
            updated_at = ?,
            last_tested_at = COALESCE(?, last_tested_at)
        WHERE id = ?
        """,
        (confidence, status.value, updated_at, last_tested_at, theory_id),
    )


def get_theory(conn: sqlite3.Connection, theory_id: str) -> Theory | None:
    row = conn.execute("SELECT * FROM theories WHERE id = ?", (theory_id,)).fetchone()
    return row_to_theory(row) if row is not None else None
