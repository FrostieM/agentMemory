"""SQL operations for the `decisions` table."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.enums import DecisionStatus
from agent_memory_lite.repositories.decisions_search import (
    date_range_clause,
    filter_rank_limit,
)


def _row_to_decision(row: sqlite3.Row) -> Decision:
    # sqlite3.Row has no .get(); handle missing column for back-compat
    # with rows from databases predating migration 0017. Note: ``in
    # row`` on sqlite3.Row checks VALUES, not column names — so the
    # presence check has to go through ``row.keys()``.
    pinned_value = row["pinned"] if "pinned" in row.keys() else 0  # noqa: SIM118
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
        pinned=bool(pinned_value),
    )


def set_decision_pinned(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    workspace_id: str,
    pinned: bool,
    updated_at: str,
) -> bool:
    """Flip the pinned flag for a decision in the given workspace.
    Returns True when a row matched (and was updated)."""
    cur = conn.execute(
        "UPDATE decisions SET pinned = ?, updated_at = ? WHERE id = ? AND workspace_id = ?",
        (1 if pinned else 0, updated_at, decision_id, workspace_id),
    )
    conn.commit()
    return cur.rowcount > 0


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


def list_active_decisions(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    query: str | None = None,
    limit: int | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[Decision]:
    extra_sql, extra_params = date_range_clause(since=since, until=until)
    rows = conn.execute(
        f"SELECT * FROM decisions "
        f"WHERE workspace_id = ? AND status = 'active' AND valid_to IS NULL {extra_sql} "
        "ORDER BY pinned DESC, importance DESC, created_at DESC",
        (workspace_id, *extra_params),
    ).fetchall()
    return filter_rank_limit(
        [_row_to_decision(r) for r in rows],
        query=query,
        limit=limit,
    )


def list_all_decisions(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    query: str | None = None,
    limit: int | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[Decision]:
    extra_sql, extra_params = date_range_clause(since=since, until=until)
    rows = conn.execute(
        f"SELECT * FROM decisions WHERE workspace_id = ? {extra_sql} "
        "ORDER BY pinned DESC, created_at DESC",
        (workspace_id, *extra_params),
    ).fetchall()
    return filter_rank_limit(
        [_row_to_decision(r) for r in rows],
        query=query,
        limit=limit,
    )
