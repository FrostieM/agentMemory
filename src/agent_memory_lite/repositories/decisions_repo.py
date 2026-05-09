"""SQL operations for the `decisions` table."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.repositories.decisions_references import (
    insert_decision_with_refs,
    serialize_references,
)
from agent_memory_lite.repositories.decisions_references import (
    row_to_decision as _row_to_decision,
)
from agent_memory_lite.repositories.decisions_search import (
    date_range_clause,
    filter_rank_limit,
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

    Uses ``with_tx`` so the write composes inside an outer transaction
    (becomes a SAVEPOINT when nested). Symmetric with
    ``ingestion.pin_service._set_table_pinned`` which was migrated in
    1.2.1; pre-1.2.1 this helper called ``conn.commit()`` directly,
    which would have prematurely flushed an outer in-flight transaction
    if a future caller pinned a decision from inside ``with_tx``.
    Returns True when a row matched (and was updated).
    """
    with with_tx(conn):
        cur = conn.execute(
            "UPDATE decisions SET pinned = ?, updated_at = ? WHERE id = ? AND workspace_id = ?",
            (1 if pinned else 0, updated_at, decision_id, workspace_id),
        )
        rowcount = cur.rowcount
    return rowcount > 0


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
    references: list[str] | None = None,
) -> None:
    """1.3.0: ``references`` is a list of file paths / ``path:symbol``
    markers serialised into ``references_json``. Tolerates pre-0027
    schemas via the helper's try/except.
    """
    insert_decision_with_refs(
        conn,
        params_without_refs=(
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
        refs_json=serialize_references(references),
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
