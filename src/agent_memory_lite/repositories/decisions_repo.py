"""SQL operations for the `decisions` table."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.enums import DecisionStatus


def _row_to_decision(row: sqlite3.Row) -> Decision:
    return Decision(
        id=row["id"],
        workspace_id=row["workspace_id"],
        title=row["title"],
        decision_text=row["decision_text"],
        rationale=row["rationale"],
        status=DecisionStatus(row["status"]),
        supersedes_decision_id=row["supersedes_decision_id"],
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert_decision_row(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    workspace_id: str,
    title: str,
    decision_text: str,
    rationale: str | None,
    supersedes_decision_id: str | None,
    source_episode_id: str | None,
    confidence: float,
    importance: float,
    valid_from: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO decisions (
            id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence, importance,
            valid_from, valid_to, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            decision_id,
            workspace_id,
            title,
            decision_text,
            rationale,
            supersedes_decision_id,
            source_episode_id,
            confidence,
            importance,
            valid_from,
            created_at,
            created_at,
        ),
    )


def close_decision(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    valid_to: str,
) -> None:
    conn.execute(
        """
        UPDATE decisions
        SET status = 'superseded', valid_to = ?, updated_at = ?
        WHERE id = ?
        """,
        (valid_to, valid_to, decision_id),
    )


def get_decision(conn: sqlite3.Connection, decision_id: str) -> Decision | None:
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _row_to_decision(row) if row is not None else None


def list_active_decisions(conn: sqlite3.Connection, workspace_id: str) -> list[Decision]:
    rows = conn.execute(
        """
        SELECT * FROM decisions
        WHERE workspace_id = ? AND status = 'active' AND valid_to IS NULL
        ORDER BY importance DESC, created_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [_row_to_decision(r) for r in rows]


def list_all_decisions(conn: sqlite3.Connection, workspace_id: str) -> list[Decision]:
    rows = conn.execute(
        "SELECT * FROM decisions WHERE workspace_id = ? ORDER BY created_at DESC",
        (workspace_id,),
    ).fetchall()
    return [_row_to_decision(r) for r in rows]
